"""Review findings: dashboard account isolation, no DB session during source wait, rating date window,
robust concurrent writes (preferences replace, adopt)."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.models.recipe import Recipe
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


@pytest.fixture(autouse=True)
def _clean_cache():
    from app.api.recipes import discover_cache

    discover_cache.clear()
    yield
    discover_cache.clear()


def _family(client, db, email):
    headers, fam = register_family(client, email)
    parent = admin_user(db, fam)
    child = add_member(db, fam, UserRole.CHILD, "Anna", date.today().year - 8)
    dash = add_member(db, fam, UserRole.DASHBOARD, "Wand", None, pin="2468")
    return headers, fam, parent, child, dash


# ---------------------------------------------------------------- (a) dashboard account is not a member


def test_all_preferences_list_hides_dashboard_account_and_is_typed(client, db_session):
    headers, fam, parent, child, dash = _family(client, db_session, "rf-list@example.ch")
    set_prefs(db_session, fam, child, likes=["Reis"], allergies=["Nuss"])
    rows = client.get("/food-preferences/", headers=headers).json()
    assert str(dash.id) not in {r["user_id"] for r in rows}
    by_id = {r["user_id"]: r for r in rows}
    assert by_id[str(child.id)] == {
        "user_id": str(child.id),
        "display_name": "Anna",
        "likes": ["Reis"],
        "dislikes": [],
        "allergies": ["Nuss"],
    }
    assert str(parent.id) in by_id and by_id[str(parent.id)]["likes"] == []


def test_all_preferences_uses_constant_number_of_queries(client, db_session):
    from sqlalchemy import event

    headers, fam, parent, child, _ = _family(client, db_session, "rf-n1@example.ch")
    for i in range(5):
        set_prefs(db_session, fam, add_member(db_session, fam, UserRole.CHILD, f"K{i}", 2015), likes=["Reis"])
    statements: list[str] = []

    def count(conn, cursor, statement, *args):
        if "user_food_preference" in statement:
            statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", count)
    try:
        assert client.get("/food-preferences/", headers=headers).status_code == 200
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", count)
    assert len(statements) == 1


def test_dashboard_cannot_be_rated_for_or_be_an_eater(client, db_session, monkeypatch):
    from app.core import recipe_discovery

    monkeypatch.setattr(recipe_discovery, "get_settings", lambda: Settings(llm_recipe_check_enabled=False))
    monkeypatch.setattr(recipe_discovery, "build_default_sources", lambda: [FakeSource([])])
    headers, fam, parent, child, dash = _family(client, db_session, "rf-dash@example.ch")
    recipe = make_recipe(db_session, fam, "Suppe", ["Karotten"])
    body = {"stars": 4, "cooked_on": date.today().isoformat(), "user_id": str(dash.id)}
    assert client.post(f"/recipes/{recipe.id}/ratings", headers=headers, json=body).status_code == 422
    resp = client.post("/recipes/discover", headers=headers, json={"eater_user_ids": [str(dash.id)]})
    assert resp.status_code == 422
    ok = client.post("/recipes/discover", headers=headers, json={"eater_user_ids": [str(child.id)]})
    assert ok.status_code == 200  # children keep working
    own = {"stars": 4, "cooked_on": date.today().isoformat(), "user_id": str(child.id)}
    assert client.post(f"/recipes/{recipe.id}/ratings", headers=headers, json=own).status_code == 201


# ---------------------------------------------------------------- (b) no DB transaction while sources run


def test_no_db_transaction_is_held_while_sources_run(client, db_session, monkeypatch):
    from app.core import recipe_discovery

    monkeypatch.setattr(recipe_discovery, "get_settings", lambda: Settings(llm_recipe_check_enabled=False))
    headers, fam, parent, child, _ = _family(client, db_session, "rf-tx@example.ch")
    set_prefs(db_session, fam, child, likes=["Reis"])
    rated = make_recipe(db_session, fam, "Reispfanne", ["Reis", "Erbsen"])
    rate(db_session, fam, rated, child, 5)
    observed: list[bool] = []

    class Stub(FakeSource):
        def search(self, ingredients, limit=5):
            observed.append(db_session.in_transaction())
            return super().search(ingredients, limit)

    monkeypatch.setattr(
        recipe_discovery, "build_default_sources", lambda: [Stub([cand("Reis Mais", ["Reis", "Mais"])])]
    )
    resp = client.post("/recipes/discover", headers=headers, json={"eater_user_ids": [str(child.id)]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert observed == [False]
    assert [r["title"] for r in body["proven"]] == ["Reispfanne"]  # loaded before the release, still returned
    assert [r["title"] for r in body["discover"]] == ["Reis Mais"]


# ---------------------------------------------------------------- (c) cooked_on window


@pytest.mark.parametrize(
    ("delta", "status"),
    [(2, 422), (1, 201), (0, 201), (-364, 201), (-366, 422), (-3000, 422)],
)
def test_cooked_on_window(client, db_session, delta, status):
    headers, fam, *_ = _family(client, db_session, f"rf-date{delta}@example.ch")
    recipe = make_recipe(db_session, fam, "Suppe", ["Karotten"])
    body = {"stars": 3, "cooked_on": (date.today() + timedelta(days=delta)).isoformat()}
    assert client.post(f"/recipes/{recipe.id}/ratings", headers=headers, json=body).status_code == status


# ---------------------------------------------------------------- (d) budgets


def test_source_budget_is_clearly_below_the_pool_wait_timeout():
    from app.core import recipe_discovery
    from app.core.recipe_sources import TheMealDB
    from app.core.recipe_sources.chefkoch import ChefkochSearchSource

    for source in (TheMealDB(), ChefkochSearchSource()):
        assert source.time_budget <= recipe_discovery.SOURCE_TIMEOUT_SECONDS - 2


# ---------------------------------------------------------------- (e) concurrent preference replace


def test_put_preferences_retries_once_after_integrity_error(client, db_session, monkeypatch):
    from app.api import food_preferences

    headers, fam, parent, child, _ = _family(client, db_session, "rf-put@example.ch")
    real = food_preferences._replace_preferences
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("insert", {}, Exception("user_food_preference_unique"))
        return real(*args, **kwargs)

    monkeypatch.setattr(food_preferences, "_replace_preferences", flaky)
    body = {"likes": ["Reis"], "dislikes": ["Pilze"], "allergies": []}
    resp = client.put(f"/food-preferences/members/{child.id}", headers=headers, json=body)
    assert resp.status_code == 200 and calls["n"] == 2
    assert client.get(f"/food-preferences/members/{child.id}", headers=headers).json()["likes"] == ["Reis"]


def test_put_preferences_gives_up_after_second_integrity_error(client, db_session, monkeypatch):
    from app.api import food_preferences

    headers, fam, parent, child, _ = _family(client, db_session, "rf-put2@example.ch")

    def always(*args, **kwargs):
        raise IntegrityError("insert", {}, Exception("user_food_preference_unique"))

    monkeypatch.setattr(food_preferences, "_replace_preferences", always)
    body = {"likes": ["Reis"], "dislikes": [], "allergies": []}
    resp = client.put(f"/food-preferences/members/{child.id}", headers=headers, json=body)
    assert resp.status_code == 409


# ---------------------------------------------------------------- (f) concurrent adopt


def test_adopt_returns_existing_recipe_when_it_loses_the_insert_race(client, db_session, monkeypatch):
    from app.api import recipes

    headers, fam = register_family(client, "rf-adopt@example.ch")
    url = "https://www.chefkoch.de/rezepte/1274521232859261/Suppe.html"
    existing = make_recipe(db_session, fam, "Suppe", ["Karotten"], source_url=url)
    real = recipes._find_family_recipe
    calls = {"n": 0}

    def blind_first_time(*args, **kwargs):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*args, **kwargs)  # the concurrent adopt is not visible yet

    monkeypatch.setattr(recipes, "_find_family_recipe", blind_first_time)
    cid = str(uuid.uuid4())
    recipes.discover_cache.put_many(fam, [(cid, cand("Suppe", ["Karotten"], url=url))])
    resp = client.post("/recipes/discover/adopt", headers=headers, json={"candidate_id": cid})
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"recipe_id": str(existing.id), "created": False, "meal_plan_entry_id": None}
    assert db_session.scalar(select(func.count()).select_from(Recipe).where(Recipe.family_id == fam)) == 1


def test_model_enforces_unique_source_url_per_family(client, db_session):
    _, fam = register_family(client, "rf-uq@example.ch")
    _, other = register_family(client, "rf-uq2@example.ch")
    url = "https://www.chefkoch.de/rezepte/1274521232859262/A.html"
    make_recipe(db_session, fam, "A", [], source_url=url)
    make_recipe(db_session, other, "A", [], source_url=url)  # other family: fine
    make_recipe(db_session, fam, "B", [])
    make_recipe(db_session, fam, "C", [])  # NULL source_url is never unique-constrained
    with pytest.raises(IntegrityError):  # last statement of the test: the session is unusable afterwards
        make_recipe(db_session, fam, "A2", [], source_url=url)
