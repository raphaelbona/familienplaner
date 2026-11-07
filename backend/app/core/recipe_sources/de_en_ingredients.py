"""German -> English ingredient names for TheMealDB (which only knows English names).

Families enter German ingredients ("Karotten"), but ``filter.php?i=`` only answers for TheMealDB's
own English names. The map is small on purpose (common kid-friendly foods); the target names were
checked against TheMealDB's ingredient list. Multi-word names use underscores as the API expects.
Terms without an entry are *skipped* for TheMealDB (no wasted request) and are never machine-translated.
Lookup is case/umlaut-insensitive and tolerant of plural endings (Karotten -> Karotte, Eier -> Ei).
"""

import unicodedata

_DE_EN: dict[str, str] = {
    # meat, fish, eggs
    "huhn": "chicken", "haehnchen": "chicken", "hahnchen": "chicken", "poulet": "chicken",
    "huhnchen": "chicken", "huhnerbrust": "chicken_breast", "haehnchenbrust": "chicken_breast",
    "pouletbrust": "chicken_breast", "rind": "beef", "rindfleisch": "beef",
    "hackfleisch": "minced_beef", "rinderhack": "minced_beef", "schwein": "pork",
    "schweinefleisch": "pork", "lamm": "lamb", "lammfleisch": "lamb", "speck": "bacon",
    "schinken": "ham", "wurst": "sausages", "wuerstchen": "sausages", "truthahn": "turkey",
    "pute": "turkey", "lachs": "salmon", "thunfisch": "tuna", "kabeljau": "cod",
    "forelle": "trout", "garnele": "prawns", "crevette": "prawns", "ei": "eggs",
    # dairy
    "milch": "milk", "butter": "butter", "kaese": "cheese", "kase": "cheese",
    "mozzarella": "mozzarella", "feta": "feta", "parmesan": "parmesan", "sahne": "cream",
    "rahm": "cream", "joghurt": "yogurt", "jogurt": "yogurt", "quark": "sweet_quark",
    "frischkaese": "cream_cheese", "frischkase": "cream_cheese", "ricotta": "ricotta",
    "mascarpone": "mascarpone",
    # pasta, grains, bread
    "nudel": "spaghetti", "pasta": "spaghetti", "spaghetti": "spaghetti", "penne": "penne_rigate",
    "makkaroni": "macaroni", "hoernchen": "macaroni", "tagliatelle": "tagliatelle",
    "lasagne": "lasagne_sheets", "reis": "rice", "couscous": "couscous", "haferflocken": "oats",
    "hafer": "oats", "mehl": "flour", "brot": "bread", "toast": "toast", "tortilla": "tortillas",
    "griess": "semolina", "gries": "semolina", "linsen": "lentils", "kichererbsen": "chickpeas",
    # vegetables
    "kartoffel": "potatoes", "erdapfel": "potatoes", "suesskartoffel": "sweet_potatoes",
    "suskartoffel": "sweet_potatoes", "karotte": "carrots", "moehre": "carrots",
    "mohre": "carrots", "rueebli": "carrots", "tomate": "tomatoes", "brokkoli": "broccoli",
    "broccoli": "broccoli", "erbse": "peas", "mais": "sweetcorn", "pilz": "mushrooms",
    "champignon": "mushrooms", "zucchini": "courgettes", "zucchetti": "courgettes",
    "kuerbis": "pumpkin", "kurbis": "pumpkin", "zwiebel": "onions", "knoblauch": "garlic",
    "spinat": "spinach", "gurke": "cucumber", "salat": "lettuce", "paprika": "red_pepper",
    "blumenkohl": "cauliflower", "lauch": "leek", "sellerie": "celery", "aubergine": "aubergine",
    "avocado": "avocado", "kohl": "cabbage", "bohne": "green_beans", "fenchel": "fennel",
    "rote bete": "beetroot", "randen": "beetroot",
    # fruit, sweet, other
    "apfel": "apples", "banane": "banana", "birne": "pears", "erdbeere": "strawberries",
    "himbeere": "raspberries", "heidelbeere": "blueberries", "orange": "orange", "zitrone": "lemon",
    "mango": "mango", "ananas": "pineapple_chunks", "pfirsich": "peaches", "honig": "honey",
    "zucker": "sugar", "schokolade": "dark_chocolate", "kakao": "cocoa", "nuss": "peanuts",
    "erdnuss": "peanuts", "mandel": "almonds", "walnuss": "walnuts", "olivenoel": "olive_oil",
    # real umlaut spellings (folded on lookup, so "Käse", "Kase" and "Kaese" all work)
    "olivenöl": "olive_oil", "öl": "vegetable_oil", "süßkartoffel": "sweet_potatoes",
    "rüebli": "carrots", "würstchen": "sausages", "hörnchen": "macaroni", "hähnchen": "chicken",
    "hähnchenbrust": "chicken_breast", "käse": "cheese", "frischkäse": "cream_cheese",
    "grieß": "semolina", "kürbis": "pumpkin", "möhre": "carrots",
    "olivenol": "olive_oil", "oel": "vegetable_oil", "tofu": "tofu", "salz": "salt",
}

# TheMealDB names accepted as-is when a family already types English ("chicken", "Tomato").
_ENGLISH = frozenset(_DE_EN.values()) | {"tomato", "potato", "carrot", "egg", "apple"}

_PLURAL_SUFFIXES = ("nen", "en", "n", "er", "e", "s")


def _fold(text: str) -> str:
    """Lowercase, ss for sharp s, umlauts and accents removed."""
    text = unicodedata.normalize("NFKD", text.strip().lower().replace("ß", "ss"))
    return "".join(c for c in text if not unicodedata.combining(c))


def _fold_key(key: str) -> str:
    return _fold(key)


_FOLDED = {_fold_key(k): v for k, v in _DE_EN.items()}
_ENGLISH_PLAIN = {name.replace("_", " ") for name in _ENGLISH}


def to_mealdb_ingredient(term: str) -> str | None:
    """TheMealDB ingredient name for ``term`` (German or already English), or None if unknown."""
    folded = _fold(term)
    if not folded or len(folded) > 60:
        return None
    candidates = [folded]
    for suffix in _PLURAL_SUFFIXES:
        if folded.endswith(suffix) and len(folded) - len(suffix) >= 2:
            candidates.append(folded[: -len(suffix)])
    for cand in candidates:
        if cand in _FOLDED:
            return _FOLDED[cand]
    spaced = folded.replace("_", " ")
    if spaced in _ENGLISH_PLAIN:
        return spaced.replace(" ", "_")
    for suffix in ("s", "es"):
        if spaced.endswith(suffix) and spaced[: -len(suffix)] in _ENGLISH_PLAIN:
            return spaced[: -len(suffix)].replace(" ", "_")
        if (spaced + suffix) in _ENGLISH_PLAIN:
            return (spaced + suffix).replace(" ", "_")
    return None
