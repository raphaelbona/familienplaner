"""Slice 5: Recipe discovery and ranking service.

Pipeline for ``discover_recipes``:
  1. load the selected eaters (must belong to the family) and their likes/dislikes/allergies;
  2. proven recipes: visible recipes this family rated, only ``is_proven`` (average > 2.0),
     never ``is_rejected``; ranked by liked-ingredient match combined with average stars;
  3. discovery: external sources (run concurrently, failures/timeouts isolated), never a
     recipe whose source_url belongs to a rejected or already proven recipe;
  4. every item is hard-filtered by allergies AND dislikes of *any* selected eater, checked
     for age suitability and a rough nutritional balance; unsuitable items are dropped;
  5. mix: up to 3 proven + discovery to fill (max 2 when 3 proven exist), total <= max_results.

Ingredient matching (``matches_term``) is case/umlaut-insensitive: both sides are tokenised
into words and compared per word; a word of 4+ letters matches inside compound words
("nuss" ~ "Haselnüsse"), while very short terms ("ei") only match the whole word or a
plural form ("Eier"), so they cannot hit "Schweinefleisch" or "Reis". A few DE/EN aliases
(e.g. nuss -> nut/walnut/almond) keep allergies from slipping through on English recipes.
Matching errs on the side of caution: false positives are acceptable, false negatives for
allergies are not.
"""

import logging
import threading
import time
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm_text import check_recipe_with_llm, is_llm_configured
from app.core.recipe_access import recipe_visible_to
from app.core.recipe_rating import calculate_rating_stats
from app.core.recipe_sources import RecipeCandidate, RecipeSource, SourceResult
from app.core.recipe_sources.health import log_source_failure, unreachable_warning
from app.models.food_preference import FoodPreferenceKind, RecipeRating, UserFoodPreference
from app.models.recipe import Ingredient, Recipe, RecipeIngredient
from app.models.user import User

logger = logging.getLogger(__name__)

SOURCE_TIMEOUT_SECONDS = 12.0  # wall-clock budget for all external sources of one discover run (<= 15 s total)
DISCOVER_TOTAL_BUDGET_SECONDS = 15.0  # after this the optional LLM stage is skipped
SOURCE_POOL_WORKERS = 8  # 4 concurrent discover runs x 2 sources: hard upper bound for source threads
MAX_LLM_CALLS_PER_DISCOVER = 4
MAX_PROVEN = 3
TARGET_TOTAL = 5  # 3 proven + 2 discovery; discovery fills up when fewer proven exist
MAX_SEARCH_TERMS = 5
_MAX_SUMMARY_CHARS = 500

# ---------------------------------------------------------------- text matching
#
# Rule table (all comparisons run on normalised text, see ``_norm``):
#   * tokens are runs of letters/digits/combining marks of ANY script, so '花生', 'фисташки'
#     or 'smør' are enforced like 'nuss';
#   * a term that yields no token at all (emoji, punctuation, stored before validation
#     existed) falls back to plain casefolded substring matching on the raw text;
#   * non-Latin tokens (CJK, Cyrillic, ...) have no word boundaries: substring either way;
#   * Latin tokens of >= 4 letters match as substring in either direction ("nuss" ~ "haselnusse");
#   * Latin tokens of 2-3 letters ("ei") only match as
#       - the whole word or an inflected form  (ei -> eier, eiern)
#       - a compound prefix  (eier + anything with >= 3 letters, ei + weiss/gelb/klar/schnee/...)
#       - a compound suffix with a stem of >= 3 letters (hunerei, vollei, wachteleier),
#         except stop words where "ei" is not the egg (brei, zwei, drei, frei, blei, ...);
#     never in the middle of a word (weisswein, reis, heidelbeeren) and not "eis" (ice).

_CHAR_MAP = str.maketrans({"ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "đ": "d", "ł": "l", "ð": "d", "þ": "th"})
_DIGRAPHS = (("ae", "a"), ("oe", "o"), ("ue", "u"))  # Nuesse == Nüsse


def _norm(text: str) -> str:
    """Casefold, fold umlauts/accents (Latin diacritics only) and ae/oe/ue digraphs."""
    text = unicodedata.normalize("NFKC", text).casefold().translate(_CHAR_MAP)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not ("̀" <= ch <= "ͯ"))  # keep kana/indic marks intact
    text = unicodedata.normalize("NFC", text)
    for digraph, single in _DIGRAPHS:
        text = text.replace(digraph, single)
    return text


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    for ch in _norm(text):
        if ch.isalnum() or unicodedata.category(ch).startswith("M"):
            current.append(ch)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _raw_fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _is_latin(token: str) -> bool:
    return all(ch.isdigit() or unicodedata.name(ch, "").startswith("LATIN") for ch in token)


_SHORT_ENDINGS = {2: ("er", "ern"), 3: ("s", "e", "n", "en", "er", "es", "ern")}
_COMPOUND_HEADS = (
    "weiss", "gelb", "klar", "schnee", "pulver", "salat", "nudel", "kuchen", "likor", "masse", "ersatz", "dotter",
)  # fmt: skip
_NOT_THE_TERM_SUFFIX = ("brei", "zwei", "drei", "frei", "blei", "schrei", "sei", "bei", "hei")


def _short_component(short: str, word: str) -> bool:
    """Short (2-3 letter) Latin term as a component of ``word`` (see rule table)."""
    endings = _SHORT_ENDINGS.get(len(short))
    if endings is None:
        return False
    forms = (short, *(short + e for e in endings))
    if word in forms:
        return True
    for stem in forms:  # compound prefix: eiweiss, eigelb, eiernudeln
        if word.startswith(stem) and any(word[len(stem):].startswith(h) for h in _COMPOUND_HEADS):
            return True
    if len(short) == 2 and word.startswith(short + "er") and len(word) - len(short) - 2 >= 3:
        return True  # plural link form: Eier + anything (Eierschwammerl is a harmless false positive)
    if any(word.endswith(x) for x in _NOT_THE_TERM_SUFFIX):
        return False
    suffix_forms = [f for f in forms if f == short or not f.endswith("s")]  # 'eis'+'s' would hit 'eiweiSS'
    return any(word.endswith(stem) and len(word) - len(stem) >= 3 for stem in suffix_forms)  # huehnerei, wachteleier


def _words_match(a: str, b: str) -> bool:
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if not (_is_latin(a) and _is_latin(b)):
        return short in long_  # no word boundaries in CJK / Cyrillic / ... text
    if len(short) >= 4:
        if short in long_:  # compound words: nuss ~ haselnusse
            return True
        # plural of a 2-letter stem as the other side: Eier ~ Huehnerei, Eiern ~ Vollei
        is_plural = (len(short) == 4 and short.endswith("er")) or (len(short) == 5 and short.endswith("ern"))
        stem = short[:2] if is_plural else ""
        return bool(stem) and _short_component(stem, long_)
    return len(short) >= 2 and _short_component(short, long_)


def _keyed(table: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    return {_norm(k): v for k, v in table.items()}


_EGG_WORDS = ("egg", "eggs", "eigelb", "eiweiss", "eiklar")
# DE/EN translation aliases: safe for allergies *and* dislikes.
_TRANSLATIONS = _keyed(
    {
        "ei": _EGG_WORDS,
        "eier": _EGG_WORDS,
        "egg": ("ei", "eier"),
        "eggs": ("ei", "eier"),
        "milch": ("milk",),
        "milk": ("milch",),
        "nuss": ("nut", "nuts"),
        "nusse": ("nut", "nuts"),
        "nut": ("nuss",),
        "nuts": ("nuss",),
        "erdnuss": ("peanut", "peanuts"),
        "erdnusse": ("peanut", "peanuts"),
        "peanut": ("erdnuss",),
        "peanuts": ("erdnuss",),
        "weizen": ("wheat",),
        "wheat": ("weizen",),
        "fisch": ("fish",),
        "fish": ("fisch",),
        "sellerie": ("celery",),
        "celery": ("sellerie",),
        "soja": ("soy", "soya"),
        "soy": ("soja",),
        "sesam": ("sesame",),
        "sesame": ("sesam",),
    }
)

_EGG_PRODUCTS = (
    "mayonnaise", "mayo", "mayonaise", "aioli", "remoulade", "baiser", "meringue", "eierlikor", "eierteigwaren",
)  # fmt: skip
_DAIRY = (
    "milch", "milk", "sahne", "cream", "rahm", "butter", "kase", "cheese", "joghurt", "yogurt", "yoghurt", "quark",
    "molke", "whey", "kefir", "schmand", "mascarpone", "ricotta", "mozzarella", "parmesan", "casein", "kasein",
    "ghee", "laktose", "lactose",
)  # fmt: skip
_NUTS = (
    "nuss", "nusse", "nut", "nuts", "haselnuss", "hazelnut", "mandel", "almond", "cashew", "kaschu", "pistazie",
    "pistachio", "walnuss", "walnut", "pecan", "macadamia", "paranuss", "erdnuss", "peanut", "pinienkern",
    "marzipan", "nougat", "nutella", "amaretto", "praline",
)  # fmt: skip
_GLUTEN = (
    "gluten", "weizen", "wheat", "dinkel", "spelt", "roggen", "rye", "gerste", "barley", "hafer", "oat", "mehl",
    "flour", "pasta", "spaghetti", "nudel", "noodle", "brot", "bread", "semmel", "brotchen", "gries", "semolina",
    "couscous", "bulgur", "seitan", "toast", "tortilla", "pizza", "knodel", "spatzle", "teig", "cracker",
    "croissant", "panade", "paniermehl", "breadcrumb",
)  # fmt: skip
_FISH = (
    "fisch", "fish", "lachs", "salmon", "thunfisch", "tuna", "forelle", "trout", "kabeljau", "dorsch", "cod",
    "hering", "herring", "sardine", "sardelle", "anchovy", "anchovis", "makrele", "mackerel", "seelachs", "zander",
    "dorade", "worcester",
)  # fmt: skip
_SHELLFISH = (
    "krebs", "crab", "krabbe", "garnele", "shrimp", "prawn", "scampi", "hummer", "lobster", "languste", "muschel",
    "mussel", "auster", "oyster", "tintenfisch", "calamari", "squid", "meeresfruchte", "seafood", "schalentier",
    "shellfish",
)  # fmt: skip
_SOY = ("soja", "soy", "soya", "tofu", "tempeh", "edamame", "miso", "tamari")
_SESAME = ("sesam", "sesame", "tahini", "tahin")
_GLUTEN_KEYS = (
    "gluten", "weizen", "wheat", "dinkel", "spelt", "roggen", "rye", "gerste", "barley", "zoliakie", "celiac",
)  # fmt: skip
_SHELLFISH_KEYS = ("krebstiere", "schalentiere", "shellfish", "crustaceans", "meeresfruchte", "seafood")

# Category aliases: only applied to ALLERGIES (a milk allergy also excludes cream and cheese).
_CATEGORIES = _keyed(
    {
        **dict.fromkeys(("ei", "eier", "egg", "eggs"), _EGG_PRODUCTS),
        **dict.fromkeys(("milch", "milk", "laktose", "lactose", "milchprodukte", "dairy"), _DAIRY),
        **dict.fromkeys(("nuss", "nusse", "nut", "nuts", "schalenfruchte", "baumnusse"), _NUTS),
        **dict.fromkeys(("erdnuss", "erdnusse", "peanut", "peanuts"), ("erdnuss", "peanut")),
        **dict.fromkeys(("haselnuss", "haselnusse", "hazelnut"), ("nougat", "nutella", "praline")),
        **dict.fromkeys(("mandel", "mandeln", "almond"), ("marzipan", "amaretto", "nougat")),
        **dict.fromkeys(_GLUTEN_KEYS, _GLUTEN),
        **dict.fromkeys(("fisch", "fish"), _FISH),
        **dict.fromkeys(_SHELLFISH_KEYS, _SHELLFISH),
        **dict.fromkeys(("soja", "soy", "soya"), _SOY),
        **dict.fromkeys(("sesam", "sesame"), _SESAME),
    }
)


def _expand(term: str, category: bool) -> list[list[str]]:
    """Token lists for the term itself plus its aliases."""
    base = _tokens(term)
    variants = [base] if base else []
    for key in (" ".join(base), *base):
        for alias in _TRANSLATIONS.get(key, ()):
            variants.append(_tokens(alias))
        if category:
            for alias in _CATEGORIES.get(key, ()):
                variants.append(_tokens(alias))
    return variants


def matches_term(term: str, text: str, category: bool = True) -> bool:
    """True if ``term`` (a preference/allergen) occurs in ``text`` (an ingredient or title).

    ``category=True`` also applies category aliases (allergies); dislikes pass False.
    """
    if not _tokens(term):  # emoji / punctuation only: plain casefolded substring
        needle = _raw_fold(term)
        return bool(needle) and needle in _raw_fold(text)
    text_tokens = _tokens(text)
    if not text_tokens:
        return False
    for variant in _expand(term, category):
        if all(any(_words_match(w, t) for t in text_tokens) for w in variant):
            return True
        if len(variant) > 1 and all(any(_words_match(t, w) for w in variant) for t in text_tokens):
            return True
    return False


def find_blocked(
    terms_per_eater: list[list[str]],
    ingredients: list[str],
    title: str | None = None,
    category: bool = True,
    extra_texts: Sequence[str] = (),
) -> str | None:
    """First term (of any eater) that matches an ingredient, the title or extra text, else None.

    ``extra_texts`` carries unstripped ingredient lines and summary text so that
    'Schokolade (enthält Haselnüsse)' is still seen after the display name was cleaned.
    """
    haystack = [*ingredients, *extra_texts, *([title] if title else [])]
    for terms in terms_per_eater:
        for term in terms:
            if any(matches_term(term, h, category) for h in haystack):
                return term
    return None


def filter_candidates_by_allergies(
    candidate_ingredients: list[str],
    eater_allergies: list[list[str]],
    title: str | None = None,
) -> bool:
    """Return False if the recipe contains an allergen of any eater (hard filter), else True."""
    return find_blocked(eater_allergies, candidate_ingredients, title) is None


# ---------------------------------------------------------------- age suitability


def _keyword_hit(keyword: str, token: str, suffix: bool = True) -> bool:
    """Whole word, word start or (compound) word end; short keywords only match plural forms."""
    if token == keyword:
        return True
    if len(keyword) >= 4:
        return token.startswith(keyword) or (suffix and token.endswith(keyword))
    return token in {keyword + s for s in ("er", "s", "e", "n")}


_HONEY = ("honig", "honey")
_NUTS = (
    "nuss", "nusse", "nut", "nuts", "walnut", "almond", "peanut", "hazelnut", "cashew", "pistachio", "pecan", "mandel",
)
_SPICY = ("chili", "cayenne", "curry", "peperoncino", "jalapeno", "sambal", "harissa", "tabasco")
_ALCOHOL = (
    "wein", "rotwein", "weisswein", "kochwein", "wine", "bier", "beer", "rum", "vodka", "whisky", "sherry",
    "brandy", "alkohol", "alcohol", "likor",
)


def _any_match(keywords: Sequence[str], ingredients: list[str], suffix: bool = True) -> bool:
    """Strict word matching (whole word / word start / word end): 'wein' never hits 'Schweinefleisch'."""
    tokens = [t for ing in ingredients for t in _tokens(ing)]
    return any(_keyword_hit(k, t, suffix) for k in keywords for t in tokens)


def check_age_suitability(eater_birth_years: list[int], ingredients: list[str]) -> tuple[bool, str]:
    """Return (is_suitable, hint). Without any known birth year nothing is restricted."""
    this_year = date.today().year
    ages = [this_year - y for y in eater_birth_years if y]
    if not ages:
        return True, ""
    min_age = min(ages)

    if min_age < 1 and _any_match(_HONEY, ingredients):
        return False, "Enthält Honig (für < 1 Jahr nicht empfohlen)"
    if min_age < 3 and _any_match(_NUTS, ingredients):
        return False, "Enthält Nüsse (für < 3 Jahre nicht empfohlen)"

    hints = []
    if min_age < 6 and _any_match(_SPICY, ingredients):
        hints.append("Könnte würzig sein (< 6 Jahre)")
    if min_age < 16 and _any_match(_ALCOHOL, ingredients, suffix=False):
        hints.append("Enthält Alkohol")
    return True, "; ".join(hints)


# ---------------------------------------------------------------- balance heuristic

_VEG = (
    "tomat carott moehr mohr zucchini brokkoli broccoli spinat paprika gurke salat lauch zwiebel onion pilz "
    "champignon kurbis aubergine erbse blumenkohl kohl fenchel sellerie mais karotte carrot spinach "
    "cucumber lettuce mushroom pumpkin cabbage cauliflower gemuse vegetable rucola fisole bohne zuckerschote"
).split()
_PROTEIN = (
    "fleisch huhn hahnchen poulet rind schwein lamm pute truthahn fisch lachs thunfisch ei eier egg eggs kase "
    "mozzarella parmesan feta gouda cheese quark joghurt tofu linse kichererbse bohne chicken beef pork fish "
    "salmon lentil bean tuna shrimp garnele schinken hackfleisch wurst"
).split()
_CARB = (
    "nudel pasta spaghetti penne reis rice kartoffel potato brot bread mehl flour couscous polenta haferflocken "
    "oat teig tortilla gnocchi bulgur hirse knodel brotchen toast"
).split()

_GROUP_LABELS = (("Gemüse", _VEG), ("Protein", _PROTEIN), ("Kohlenhydrate", _CARB))


def assess_balance(ingredients: list[str]) -> tuple[bool, str]:
    """Rough vegetable / protein / carbohydrate check. Balanced = all three groups present."""
    tokens = [t for ing in ingredients for t in _tokens(ing)]
    missing = [
        label for label, keywords in _GROUP_LABELS if not any(_keyword_hit(k, t) for k in keywords for t in tokens)
    ]
    if not missing:
        return True, "Ausgewogen"
    return False, "Ergänzen: " + ", ".join(missing)


# ---------------------------------------------------------------- ranking helpers


def compute_ingredient_weights(eater_likes: list[list[str]]) -> dict[str, float]:
    """More eaters liking an ingredient = higher weight (keys are lowercase)."""
    weights: dict[str, float] = defaultdict(float)
    total_eaters = len(eater_likes)
    for likes in eater_likes:
        for ingredient in likes:
            weights[ingredient.lower()] += 1.0 / max(1, total_eaters)
    return dict(weights)


def like_score(weights: dict[str, float], ingredients: list[str]) -> float:
    """Weighted share (0..1) of the liked ingredients that this recipe contains."""
    total = sum(weights.values())
    if total <= 0:
        return 0.0
    hit = sum(w for term, w in weights.items() if any(matches_term(term, ing) for ing in ingredients))
    return hit / total


def combined_score(avg_stars: float | None, likes: float) -> float:
    stars_norm = ((avg_stars - 1) / 4) if avg_stars is not None else 0.0
    return 0.5 * stars_norm + 0.5 * likes


def build_default_sources() -> list[RecipeSource]:
    """TheMealDB always; site-search sources (Chefkoch) for allowlisted domains if ``recipe_search_enabled``."""
    from app.core.recipe_sources import TheMealDB
    from app.core.recipe_sources.chefkoch import SITE_SOURCES
    from app.core.recipe_sources.url_policy import parse_domains

    sources: list[RecipeSource] = [TheMealDB()]
    settings = get_settings()
    if settings.recipe_search_enabled:
        for domain in parse_domains(settings.recipe_search_domains):
            if domain in SITE_SOURCES:  # domains without an implementation are only allowlisted for adoption
                sources.append(SITE_SOURCES[domain]())
    return sources


# ---------------------------------------------------------------- service

# One shared, bounded pool: a slow source can never spawn additional threads (no pile-up),
# and the adapters enforce their own total deadline so stuck workers free up on their own.
_pool_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None


def _source_pool() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=SOURCE_POOL_WORKERS, thread_name_prefix="recipe-source")
        return _pool



def _source_name(src: object) -> str:
    return getattr(src, "display_name", None) or type(src).__name__


def _search_with_status(src: RecipeSource, terms: list[str]) -> SourceResult:
    """Sources without ``search_with_status`` (duck-typed fakes) count as healthy unless they raise."""
    method = getattr(src, "search_with_status", None)
    if method is not None:
        return method(terms, 10)
    return SourceResult(list(src.search(terms, 10)))


class RecipeDiscoveryService:
    """Service for discovering and ranking recipes."""

    def __init__(self, sources: Sequence[RecipeSource] | None = None) -> None:
        self._sources = list(sources) if sources is not None else None

    def discover_recipes(
        self,
        db: Session,
        family_id: uuid.UUID,
        eater_user_ids: Sequence[uuid.UUID],
        meal_type: str,
        extra_ingredients: list[str] | None = None,
        max_results: int = 8,
    ) -> dict:
        """Return {"proven": [...], "discover": [...]} (see module docstring)."""
        result, _ = self.discover_with_candidates(
            db, family_id, eater_user_ids, meal_type, extra_ingredients, max_results
        )
        return result

    def discover_with_candidates(
        self,
        db: Session,
        family_id: uuid.UUID,
        eater_user_ids: Sequence[uuid.UUID],
        meal_type: str,
        extra_ingredients: list[str] | None = None,
        max_results: int = 8,
    ) -> tuple[dict, list[RecipeCandidate]]:
        """Like ``discover_recipes`` but also returns the candidate objects aligned with result["discover"].

        ``meal_type`` is validated by the API layer; it does not influence ranking yet.
        """
        family_id = _as_uuid(family_id)
        eaters = []
        for uid in eater_user_ids:
            user = db.get(User, _as_uuid(uid))
            if user is not None and user.family_id == family_id:
                eaters.append(user)
        if not eaters:
            return {"proven": [], "discover": [], "source_warnings": []}, []

        eater_likes: list[list[str]] = []
        eater_dislikes: list[list[str]] = []
        eater_allergies: list[list[str]] = []
        for eater in eaters:
            prefs = list(
                db.scalars(
                    select(UserFoodPreference).where(
                        UserFoodPreference.user_id == eater.id, UserFoodPreference.family_id == family_id
                    )
                )
            )
            eater_likes.append([p.ingredient_name for p in prefs if p.kind == FoodPreferenceKind.LIKES])
            eater_dislikes.append([p.ingredient_name for p in prefs if p.kind == FoodPreferenceKind.DISLIKES])
            eater_allergies.append([p.ingredient_name for p in prefs if p.kind == FoodPreferenceKind.ALLERGY])

        weights = compute_ingredient_weights(eater_likes)
        for extra in extra_ingredients or []:
            weights[extra.lower()] = weights.get(extra.lower(), 0.0) + 1.0

        birth_years = [e.birth_year for e in eaters if e.birth_year]
        ctx = _Context(
            allergies=eater_allergies,
            dislikes=eater_dislikes,
            weights=weights,
            birth_years=birth_years,
            settings=get_settings(),
        )

        proven, blocked_urls = self._proven_recipes(db, family_id, ctx)
        proven_items: list[dict] = []
        for entry in proven:
            item = self._format_proven(entry, ctx)
            if item["age_ok"]:  # the optional LLM may still veto an item
                proven_items.append(item)
            if len(proven_items) >= MAX_PROVEN:
                break

        # Everything from the database is now plain data: end the read transaction so no connection is
        # pinned while the external sources are awaited (up to SOURCE_TIMEOUT_SECONDS).
        db.commit()
        candidates, source_warnings = self._discover_external(ctx, blocked_urls, extra_ingredients or [], eater_likes)

        if len(proven_items) >= MAX_PROVEN:
            slots = min(2, max_results - len(proven_items))
        else:
            slots = min(TARGET_TOTAL - len(proven_items), max_results - len(proven_items))

        discover_items: list[dict] = []
        chosen: list[RecipeCandidate] = []
        for cand in candidates:
            if len(discover_items) >= slots:
                break
            item = self._format_candidate(cand, ctx)
            if item["age_ok"]:
                discover_items.append(item)
                chosen.append(cand)
        result = {"proven": proven_items, "discover": discover_items, "source_warnings": source_warnings}
        return result, chosen

    # -- proven -----------------------------------------------------------------

    def _proven_recipes(self, db: Session, family_id: uuid.UUID, ctx: "_Context") -> tuple[list[dict], set[str]]:
        """Return (ranked proven recipes, source_urls that must not be re-suggested)."""
        ratings_by_recipe: dict[uuid.UUID, list[int]] = defaultdict(list)
        for recipe_id, stars in db.execute(
            select(RecipeRating.recipe_id, RecipeRating.stars).where(RecipeRating.family_id == family_id)
        ):
            ratings_by_recipe[recipe_id].append(stars)
        if not ratings_by_recipe:
            return [], set()

        recipes = list(
            db.scalars(
                select(Recipe).where(Recipe.id.in_(ratings_by_recipe), recipe_visible_to(family_id))
            )
        )
        names_by_recipe: dict[uuid.UUID, list[str]] = defaultdict(list)
        for recipe_id, name in db.execute(
            select(RecipeIngredient.recipe_id, Ingredient.name)
            .join(Ingredient, Ingredient.id == RecipeIngredient.ingredient_id)
            .where(RecipeIngredient.recipe_id.in_([r.id for r in recipes]))
            .order_by(Ingredient.name)
        ):
            names_by_recipe[recipe_id].append(name)

        ranked = []
        excluded_urls: set[str] = set()
        for recipe in recipes:
            stats = calculate_rating_stats(ratings_by_recipe[recipe.id])
            if recipe.source_url:
                excluded_urls.add(recipe.source_url)  # rejected or proven: never re-suggest as discovery
            if stats["is_rejected"] or not stats["is_proven"]:
                continue
            ingredients = names_by_recipe.get(recipe.id, [])
            if ctx.blocked(ingredients, recipe.name, [recipe.instructions[:_MAX_SUMMARY_CHARS]]):
                continue
            age_ok, age_hint = check_age_suitability(ctx.birth_years, ingredients)
            if not age_ok:
                continue
            likes = like_score(ctx.weights, ingredients)
            ranked.append(
                {
                    "recipe": recipe,
                    "ingredients": ingredients,
                    "avg_stars": stats["average"],
                    "rating_count": stats["rating_count"],
                    "match_score": likes,
                    "score": combined_score(stats["average"], likes),
                    "age_hint": age_hint,
                }
            )
        ranked.sort(key=lambda r: (-r["score"], -r["rating_count"], r["recipe"].name))
        return ranked, excluded_urls

    # -- external discovery -----------------------------------------------------

    def _discover_external(
        self, ctx: "_Context", excluded_urls: set[str], extra: list[str], eater_likes: list[list[str]]
    ) -> tuple[list[RecipeCandidate], list[str]]:
        terms: list[str] = []
        for term in [*extra, *(t for likes in eater_likes for t in likes)]:
            term = term.strip()
            if term and term.lower() not in {t.lower() for t in terms}:
                if ctx.blocked([term]) is None:  # never search for something blocked
                    terms.append(term)
        # Most wanted first (weight = share of eaters liking it, +1 per explicit extra): sources that only
        # search one term (Chefkoch) then use the best one. sort() is stable: input order breaks ties.
        terms.sort(key=lambda t: -ctx.weights.get(t.lower(), 0.0))
        terms = terms[:MAX_SEARCH_TERMS]

        raw, warnings = self._run_sources(terms)

        seen_urls = set(excluded_urls)
        accepted: list[tuple[float, RecipeCandidate]] = []
        for cand in raw:
            if not cand.title or not cand.ingredients:
                continue
            if cand.source_url and cand.source_url in seen_urls:
                continue
            seen_urls.add(cand.source_url)
            if ctx.blocked(cand.ingredients, cand.title, _candidate_extra_texts(cand)):
                continue
            age_ok, _ = check_age_suitability(ctx.birth_years, [*cand.ingredients, *cand.raw_ingredients])
            if not age_ok:
                continue
            accepted.append((like_score(ctx.weights, cand.ingredients), cand))
        accepted.sort(key=lambda pair: -pair[0])  # stable: source order breaks ties
        return [c for _, c in accepted], warnings

    def _run_sources(self, terms: list[str]) -> tuple[list[RecipeCandidate], list[str]]:
        """Query all sources concurrently; a failing or slow source never breaks the result.

        Returns (candidates, source_warnings). A warning is added only if a source failed
        (unreachable, timeout, non-200, crash) - never if it simply found nothing.
        """
        sources = self._sources if self._sources is not None else build_default_sources()
        if not sources:
            return [], []
        pool = _source_pool()
        futures = {pool.submit(_search_with_status, src, terms): src for src in sources}
        done, not_done = wait(futures, timeout=SOURCE_TIMEOUT_SECONDS)
        warnings: list[str] = []
        for fut, src in futures.items():  # source order, not the arbitrary set order of ``not_done``
            if fut not in not_done:
                continue
            fut.cancel()  # queued-but-unstarted work must not run later; running sources end at their own deadline
            log_source_failure(_source_name(src), "timeout")
            warnings.append(unreachable_warning(_source_name(src)))
        results: list[RecipeCandidate] = []
        for fut, src in futures.items():  # submission order == source order: deterministic
            if fut not in done:
                continue
            try:
                outcome = fut.result()
            except Exception as exc:
                log_source_failure(_source_name(src), exc)
                warnings.append(unreachable_warning(_source_name(src)))
                continue
            results.extend(outcome.candidates)
            if outcome.failed:  # the adapter already logged the details
                warnings.append(outcome.warning or unreachable_warning(_source_name(src)))
        return results, warnings

    # -- formatting ---------------------------------------------------------------

    def _apply_llm(self, ctx: "_Context", title: str, ingredients: list[str], base: dict) -> dict | None:
        """Merge an optional LLM verdict into ``base``; None means "drop this item"."""
        settings = ctx.settings
        if ctx.llm_broken or not ctx.birth_years or not is_llm_configured(settings):
            return base
        if ctx.llm_calls >= MAX_LLM_CALLS_PER_DISCOVER or ctx.elapsed() > DISCOVER_TOTAL_BUDGET_SECONDS:
            return base  # per-request cap / overall deadline: rule-based verdict only
        ctx.llm_calls += 1
        youngest = date.today().year - max(ctx.birth_years)
        verdict = check_recipe_with_llm(youngest, title, ingredients, settings)
        if verdict is None:
            ctx.llm_broken = True  # do not keep waiting on a dead provider for every item
            return base
        if verdict["age_ok"] is False:
            return None
        merged = dict(base)
        if verdict["age_hint"]:
            merged["age_hint"] = verdict["age_hint"]
        merged["balanced"] = verdict["balanced"]
        merged["balance_label"] = "Ausgewogen" if verdict["balanced"] else "Nicht ausgewogen"
        return merged

    def _assess(self, ctx: "_Context", title: str, ingredients: list[str], age_hint: str) -> dict:
        balanced, label = assess_balance(ingredients)
        base = {"age_ok": True, "age_hint": age_hint, "balanced": balanced, "balance_label": label}
        merged = self._apply_llm(ctx, title, ingredients, base)
        if merged is None:
            base["age_ok"] = False
            return base
        return merged

    def _format_proven(self, entry: dict, ctx: "_Context") -> dict:
        recipe: Recipe = entry["recipe"]
        assessment = self._assess(ctx, recipe.name, entry["ingredients"], entry["age_hint"])
        return {
            "id": str(recipe.id),
            "language": "de",
            "title": recipe.name,
            "image_url": recipe.image_url,
            "prep_minutes": recipe.prep_minutes,
            "source_name": recipe.source_name or "Family Recipe",
            "source_url": recipe.source_url,
            "ingredients": entry["ingredients"],
            "avg_stars": entry["avg_stars"],
            "rating_count": entry["rating_count"],
            "match_score": round(entry["match_score"], 3),
            **assessment,
        }

    def _format_candidate(self, cand: RecipeCandidate, ctx: "_Context") -> dict:
        _, age_hint = check_age_suitability(ctx.birth_years, [*cand.ingredients, *cand.raw_ingredients])
        assessment = self._assess(ctx, cand.title, cand.ingredients, age_hint)
        return {
            "title": cand.title,
            "image_url": cand.image_url,
            "prep_minutes": cand.prep_minutes,
            "source_name": cand.source_name,
            "source_url": cand.source_url,
            "ingredients": list(cand.ingredients),
            "avg_stars": None,
            "rating_count": 0,
            "match_score": round(like_score(ctx.weights, cand.ingredients), 3),
            "language": cand.language,
            **assessment,
        }


class _Context:
    def __init__(self, allergies, dislikes, weights, birth_years, settings) -> None:
        self.allergies = allergies  # one list per selected eater
        self.dislikes = dislikes
        self.weights = weights
        self.birth_years = birth_years
        self.settings = settings
        self.llm_broken = False
        self.llm_calls = 0
        self._started = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def blocked(
        self, ingredients: list[str], title: str | None = None, extra_texts: Sequence[str] = ()
    ) -> str | None:
        """Allergies (with category aliases) and dislikes of ANY selected eater are hard filters."""
        return find_blocked(self.allergies, ingredients, title, extra_texts=extra_texts) or find_blocked(
            self.dislikes, ingredients, title, category=False, extra_texts=extra_texts
        )


def _candidate_extra_texts(cand: RecipeCandidate) -> list[str]:
    """Unstripped ingredient lines plus the short summary: checked against allergies/dislikes."""
    summary = [cand.instructions_summary[:_MAX_SUMMARY_CHARS]] if cand.instructions_summary else []
    return [*cand.raw_ingredients, *summary]


def _as_uuid(value: "uuid.UUID | str") -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
