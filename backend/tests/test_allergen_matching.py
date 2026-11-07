"""Allergen safety: unicode-aware matching, German compound rules, aliases, raw ingredient text.

Rule table for short terms (<= 3 letters after normalisation, Latin script) -- see
``app.core.recipe_discovery``: the term must be a whole word, an inflected form (Ei -> Eier/Eiern),
a compound prefix (Eiernudeln, Eigelb, Eiweiss) or a compound suffix with a stem of >= 3 letters
(Huehnerei, Vollei, Wachteleier). It must NOT match inside a word (Weisswein, Reis, Heidelbeeren).
False positives are acceptable, false negatives are not.
"""

from datetime import date

import pytest

from app.core.config import Settings
from app.core.recipe_discovery import find_blocked, matches_term
from app.core.recipe_sources import RecipeCandidate
from app.core.recipe_sources.json_ld_parser import parse_json_ld_recipe
from app.models.user import UserRole
from tests.food_helpers import (
    FakeSource,
    add_member,
    admin_user,
    cand,
    make_recipe,
    rate,
    register_family,
    set_prefs,
)

THIS_YEAR = date.today().year

BLOCKED = [
    # --- M1: non-ASCII scripts and symbols
    ("花生", "花生酱"),
    ("花生", "Kekse mit 花生"),
    ("фисташки", "Мороженое с ФИСТАШКИ"),
    ("Фисташки", "фисташки"),
    ("smør", "Smør 100 g"),
    ("Smør", "smor"),
    ("æg", "3 Æg"),
    ("🥜", "Kuchen 🥜 mit Zucker"),
    ("!!", "Vorsicht!! scharf"),
    # --- M2: 'Ei' as compound component
    ("Ei", "Ei"),
    ("Ei", "Eier"),
    ("Ei", "Eiern"),
    ("Ei", "Hühnerei"),
    ("Ei", "Eiernudeln"),
    ("Ei", "Vollei"),
    ("Ei", "Eigelb"),
    ("Ei", "Eiweiss"),
    ("Ei", "Eiweiß"),
    ("Ei", "Spiegelei"),
    ("Ei", "Wachteleier"),
    ("Ei", "Eierkuchen"),
    ("Ei", "Eggs"),
    ("Hühnerei", "Eier"),
    # --- aliases: egg
    ("Ei", "Mayonnaise"),
    ("Ei", "Mayo"),
    ("egg", "mayonnaise"),
    ("Ei", "Aioli"),
    # --- gluten / wheat
    ("Gluten", "Pasta"),
    ("Gluten", "Spaghetti"),
    ("Gluten", "Brot"),
    ("Gluten", "Mehl"),
    ("Gluten", "Semmelbrösel"),
    ("Gluten", "Grieß"),
    ("Gluten", "Couscous"),
    ("Gluten", "Bulgur"),
    ("Weizen", "Mehl"),
    ("Weizen", "Vollkornbrot"),
    ("Weizen", "Spaghetti"),
    ("Weizen", "Weizengriess"),
    ("wheat", "flour"),
    ("gluten", "bread"),
    # --- milk family
    ("Milch", "Sahne"),
    ("Milch", "Rahm"),
    ("Milch", "Butter"),
    ("Milch", "Käse"),
    ("Milch", "Joghurt"),
    ("Milch", "Quark"),
    ("Milch", "Molke"),
    ("Milch", "Parmesan"),
    ("Laktose", "Schlagsahne"),
    ("milk", "cheddar cheese"),
    ("milk", "yoghurt"),
    # --- nuts
    ("Nüsse", "Haselnuss"),
    ("Nüsse", "Mandeln"),
    ("Nüsse", "Cashewkerne"),
    ("Nüsse", "Pistazien"),
    ("Nüsse", "Walnüsse"),
    ("Nüsse", "Erdnuss"),
    ("Nüsse", "Marzipan"),
    ("Nüsse", "Nougat"),
    ("Erdnuss", "Erdnussbutter-Cookies"),
    ("Erdnuss", "peanut butter"),
    ("Haselnuss", "Nougatcreme"),
    ("Mandel", "Marzipan"),
    ("nuts", "walnuts"),
    # --- fish / shellfish
    ("Fisch", "Lachs"),
    ("Fisch", "Thunfisch"),
    ("Fisch", "Salmon fillet"),
    ("Fisch", "Sardellen"),
    ("Krebstiere", "Garnelen"),
    ("Schalentiere", "Hummer"),
    ("Shellfish", "Shrimp"),
    ("Shellfish", "Prawns"),
    # --- soy / sesame
    ("Soja", "Tofu"),
    ("Soja", "Sojasauce"),
    ("soy", "soy sauce"),
    ("Sesam", "Tahini"),
    ("Sesam", "Sesamöl"),
    ("sesame", "sesame seeds"),
]

NOT_BLOCKED = [
    ("Ei", "Reis"),
    ("Ei", "Weisswein"),
    ("Ei", "Weißwein"),
    ("Ei", "Schweinefleisch"),
    ("Ei", "Eis"),
    ("Ei", "Eisberg"),
    ("Ei", "Heidelbeeren"),
    ("Ei", "Kartoffelbrei"),
    ("Ei", "Haferbrei"),
    ("Ei", "zwei Zwiebeln"),
    ("Ei", "Feigen"),
    ("Ei", "Tomaten"),
    ("Ei", "Sahne"),
    ("Nüsse", "Reis"),
    ("Nüsse", "Tomaten"),
    ("Gluten", "Reis"),
    ("Gluten", "Kartoffeln"),
    ("Gluten", "Tomaten"),
    ("Milch", "Tomaten"),
    ("Milch", "Reis"),
    ("Fisch", "Hähnchen"),
    ("Fisch", "Reis"),
    ("Soja", "Reis"),
    ("Sesam", "Reis"),
    ("花生", "Reis"),
    ("фисташки", "Reis"),
    ("smør", "Reis"),
]


@pytest.mark.parametrize(("term", "text"), BLOCKED)
def test_blocked_as_allergy(term, text):
    assert matches_term(term, text, category=True), f"{term!r} must block {text!r}"


@pytest.mark.parametrize(("term", "text"), NOT_BLOCKED)
def test_not_blocked_as_allergy(term, text):
    assert not matches_term(term, text, category=True), f"{term!r} must not block {text!r}"


def test_dislikes_do_not_use_category_aliases():
    assert not matches_term("Milch", "Butter", category=False)
    assert matches_term("Butter", "Butter", category=False)
    assert matches_term("花生", "花生酱", category=False)


def test_emoji_only_term_still_enforced_via_substring_fallback():
    assert find_blocked([["🥜"]], ["Kuchen"], title="Kekse 🥜") == "🥜"
    assert find_blocked([["🥜"]], ["Kuchen"], title="Kekse") is None


def test_find_blocked_checks_extra_texts():
    assert find_blocked([["Haselnüsse"]], ["Schokolade"], extra_texts=["Schokolade (enthält Haselnüsse)"])
    assert find_blocked([["Erdnuss"]], ["Zucker"], title="Erdnussbutter-Cookies")
    assert find_blocked([["Erdnuss"]], ["Zucker"], extra_texts=["Mit Erdnüssen bestreuen"])


# ---------------------------------------------------------------- raw ingredient lines (parser)


def test_parser_keeps_full_raw_ingredient_lines():
    rec = parse_json_ld_recipe(
        {
            "@type": "Recipe",
            "name": "Dessert",
            "recipeIngredient": ["100 g Schokolade (enthält Haselnüsse)", "Zucker, evtl. Erdnüsse"],
        }
    )
    assert rec.ingredients == ["Schokolade", "Zucker"]  # display names stay stripped
    assert "100 g Schokolade (enthält Haselnüsse)" in rec.raw_ingredients
    assert "Zucker, evtl. Erdnüsse" in rec.raw_ingredients


def test_recipe_candidate_defaults_raw_ingredients_to_empty():
    c = RecipeCandidate("t", None, ["x"], None, None, "https://x", "s")
    assert c.raw_ingredients == []


# ---------------------------------------------------------------- service level (external + family recipes)


@pytest.fixture(autouse=True)
def _default_settings(monkeypatch):
    from app.core import recipe_discovery

    monkeypatch.setattr(recipe_discovery, "get_settings", lambda: Settings(llm_recipe_check_enabled=False))


def _setup(client, db, email, allergies=(), dislikes=()):
    headers, fam = register_family(client, email)
    child = add_member(db, fam, UserRole.CHILD, "Anna", THIS_YEAR - 10)
    set_prefs(db, fam, child, allergies=allergies, dislikes=dislikes)
    return headers, fam, child


def _discover(client, headers, child, monkeypatch, candidates):
    from app.core import recipe_discovery

    monkeypatch.setattr(recipe_discovery, "build_default_sources", lambda: [FakeSource(candidates)])
    resp = client.post("/recipes/discover", headers=headers, json={"eater_user_ids": [str(child.id)]})
    assert resp.status_code == 200, resp.text
    return [i["title"] for i in resp.json()["discover"]]


def test_external_candidate_with_allergen_only_in_parenthetical_is_blocked(client, db_session, monkeypatch):
    headers, fam, child = _setup(client, db_session, "am-raw@example.ch", allergies=["Nüsse"])
    unsafe = cand("Schoko-Dessert", ["Schokolade", "Sahne"])
    unsafe.raw_ingredients = ["100 g Schokolade (enthält Haselnüsse)", "Sahne"]
    safe = cand("Reis Pfanne", ["Reis", "Erbsen"])
    assert _discover(client, headers, child, monkeypatch, [unsafe, safe]) == ["Reis Pfanne"]


def test_external_candidate_blocked_by_title(client, db_session, monkeypatch):
    headers, fam, child = _setup(client, db_session, "am-title@example.ch", allergies=["Erdnuss"])
    unsafe = cand("Erdnussbutter-Cookies", ["Zucker", "Mehl"])
    safe = cand("Reis Pfanne", ["Reis", "Erbsen"])
    assert _discover(client, headers, child, monkeypatch, [unsafe, safe]) == ["Reis Pfanne"]


def test_external_candidate_blocked_by_summary(client, db_session, monkeypatch):
    headers, fam, child = _setup(client, db_session, "am-summary@example.ch", allergies=["Erdnuss"])
    unsafe = cand("Keksbaum", ["Zucker", "Mehl"])
    unsafe.instructions_summary = "Mit gehackten Erdnüssen bestreuen."
    safe = cand("Reis Pfanne", ["Reis", "Erbsen"])
    assert _discover(client, headers, child, monkeypatch, [unsafe, safe]) == ["Reis Pfanne"]


def test_non_ascii_allergy_is_enforced_end_to_end(client, db_session, monkeypatch):
    headers, fam, child = _setup(client, db_session, "am-cjk@example.ch", allergies=["花生", "фисташки", "🥜"])
    cands = [
        cand("Wok", ["Reis", "花生酱"]),
        cand("Eis", ["Milch", "Фисташки"]),
        cand("Kekse", ["Mehl"], url="https://www.chefkoch.de/rezepte/1274521232862621/K.html"),
        cand("Reis Pfanne", ["Reis", "Erbsen"]),
    ]
    cands[2].title = "Kekse 🥜"
    assert _discover(client, headers, child, monkeypatch, cands) == ["Reis Pfanne"]


def test_egg_allergy_blocks_compounds_and_mayo_but_not_rice(client, db_session, monkeypatch):
    headers, fam, child = _setup(client, db_session, "am-egg@example.ch", allergies=["Ei"])
    cands = [
        cand("A", ["Hühnerei", "Salat"]),
        cand("B", ["Eiernudeln", "Sahne"]),
        cand("C", ["Mayonnaise", "Kartoffeln"]),
        cand("D", ["Reis", "Weisswein", "Erbsen"]),
    ]
    assert _discover(client, headers, child, monkeypatch, cands) == ["D"]


def test_family_recipe_with_compound_allergen_is_not_proven_for_allergic_eater(client, db_session, monkeypatch):
    from app.core import recipe_discovery

    monkeypatch.setattr(recipe_discovery, "build_default_sources", lambda: [])
    headers, fam, child = _setup(client, db_session, "am-fam@example.ch", allergies=["Ei"])
    parent = admin_user(db_session, fam)
    unsafe = make_recipe(db_session, fam, "Rührei-Brot", ["Hühnerei", "Butter"])
    safe = make_recipe(db_session, fam, "Reis Pfanne", ["Reis", "Erbsen"])
    for r in (unsafe, safe):
        rate(db_session, fam, r, parent, 5)
    resp = client.post("/recipes/discover", headers=headers, json={"eater_user_ids": [str(child.id)]})
    assert [i["title"] for i in resp.json()["proven"]] == ["Reis Pfanne"]


def test_family_recipe_blocked_by_instructions_text(client, db_session, monkeypatch):
    from app.core import recipe_discovery

    monkeypatch.setattr(recipe_discovery, "build_default_sources", lambda: [])
    headers, fam, child = _setup(client, db_session, "am-instr@example.ch", allergies=["Erdnuss"])
    parent = admin_user(db_session, fam)
    unsafe = make_recipe(db_session, fam, "Kekse", ["Mehl"])
    unsafe.instructions = "Backen und mit Erdnüssen bestreuen"
    db_session.commit()
    rate(db_session, fam, unsafe, parent, 5)
    resp = client.post("/recipes/discover", headers=headers, json={"eater_user_ids": [str(child.id)]})
    assert resp.json()["proven"] == []


# ---------------------------------------------------------------- API validation of preference names


@pytest.mark.parametrize("bad", ["🥜", "!!!", "---", "🥜🥜", "   ..."])
def test_preference_without_letters_or_digits_is_rejected(client, db_session, bad):
    headers, fam, child = _setup(client, db_session, f"am-422-{abs(hash(bad))}@example.ch")
    for kind in ("likes", "dislikes", "allergies"):
        resp = client.put(f"/food-preferences/members/{child.id}", headers=headers, json={kind: [bad]})
        assert resp.status_code == 422, (kind, bad, resp.text)


@pytest.mark.parametrize("good", ["花生", "фисташки", "smør", "Ei", "7up", "Nüsse"])
def test_preference_in_any_script_is_accepted(client, db_session, good):
    headers, fam, child = _setup(client, db_session, f"am-ok-{abs(hash(good))}@example.ch")
    resp = client.put(f"/food-preferences/members/{child.id}", headers=headers, json={"allergies": [good]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["allergies"] == [good]


def test_blank_preference_entries_are_still_silently_dropped(client, db_session):
    headers, fam, child = _setup(client, db_session, "am-blank@example.ch")
    resp = client.put(f"/food-preferences/members/{child.id}", headers=headers, json={"likes": ["  ", "Reis"]})
    assert resp.status_code == 200
    assert resp.json()["likes"] == ["Reis"]
