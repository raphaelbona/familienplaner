"""Adopt endpoint: end to end, dedupe, tenancy, validation and sanitising."""

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.models.meal_plan import MealPlanEntry
from app.models.recipe import Ingredient, Recipe, RecipeIngredient
from app.models.user import UserRole
from tests.food_helpers import FakeSource, add_member, cand, pin_headers, register_family


@pytest.fixture(autouse=True)
def _clean_cache():
    from app.api.recipes import discover_cache

    discover_cache.clear()
    yield
    discover_cache.clear()


def _stash(family_id, candidate):
    from app.api.recipes import discover_cache

    cid = str(uuid.uuid4())
    discover_cache.put_many(family_id, [(cid, candidate)])
    return cid


def _adopt(client, headers, **body):
    return client.post("/recipes/discover/adopt", headers=headers, json=body)


def test_discover_then_adopt_end_to_end(client, db_session, monkeypatch):
    from app.core import recipe_discovery
    from app.core.config import Settings

    headers, fam = register_family(client, "adopt-e2e@example.ch")
    child = add_member(db_session, fam, UserRole.CHILD, "Anna", date.today().year - 8)
    monkeypatch.setattr(recipe_discovery, "get_settings", lambda: Settings(llm_recipe_check_enabled=False))
    monkeypatch.setattr(
        recipe_discovery,
        "build_default_sources",
        lambda: [FakeSource([cand("Pasta Pomodoro", ["Pasta", "Tomaten"], image="https://img.example/p.jpg")])],
    )
    found = client.post(
        "/recipes/discover",
        headers=headers,
        json={"eater_user_ids": [str(child.id)], "meal_type": "dinner", "extra_ingredients": ["Pasta"]},
    ).json()
    cid = found["discover"][0]["candidate_id"]
    resp = _adopt(client, headers, candidate_id=cid, plan_date="2030-01-05", meal_type="dinner")
    assert resp.status_code == 201, resp.text
    recipe_id = resp.json()["recipe_id"]
    recipe = db_session.get(Recipe, uuid.UUID(recipe_id))
    assert recipe.family_id == fam and recipe.name == "Pasta Pomodoro"
    assert recipe.image_url == "https://img.example/p.jpg"
    names = sorted(
        db_session.scalars(
            select(Ingredient.name).join(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id)
        )
    )
    assert names == ["Pasta", "Tomaten"]
    entries = client.get("/meal-plan/entries", headers=headers).json()
    assert [(e["date"], e["meal_type"], e["recipe_name"]) for e in entries] == [
        ("2030-01-05", "dinner", "Pasta Pomodoro")
    ]
    assert recipe_id in {r["id"] for r in client.get("/recipes", headers=headers).json()}


def test_adopt_dedupes_by_source_url_but_still_plans(client, db_session):
    headers, fam = register_family(client, "adopt-dedupe@example.ch")
    c = cand("Suppe", ["Karotten"], url="https://www.chefkoch.de/rezepte/1274521232859261/Suppe.html")
    first = _adopt(client, headers, candidate_id=_stash(fam, c))
    second = _adopt(client, headers, candidate_id=_stash(fam, c), plan_date="2030-02-01", meal_type="lunch")
    assert first.status_code == second.status_code == 201
    assert first.json()["recipe_id"] == second.json()["recipe_id"]
    count = db_session.scalar(select(func.count()).select_from(Recipe).where(Recipe.family_id == fam))
    assert count == 1
    entries = db_session.scalars(select(MealPlanEntry).where(MealPlanEntry.family_id == fam)).all()
    assert len(entries) == 1 and entries[0].meal_type == "lunch"


def test_dedupe_is_per_family(client, db_session):
    ha, fa = register_family(client, "adopt-fam-a@example.ch")
    hb, fb = register_family(client, "adopt-fam-b@example.ch")
    c = cand("Suppe", ["Karotten"], url="https://www.chefkoch.de/rezepte/1274521232859261/Suppe.html")
    ra = _adopt(client, ha, candidate_id=_stash(fa, c)).json()["recipe_id"]
    rb = _adopt(client, hb, candidate_id=_stash(fb, c)).json()["recipe_id"]
    assert ra != rb
    assert db_session.get(Recipe, uuid.UUID(rb)).family_id == fb


def test_other_family_cannot_adopt_candidate(client, db_session):
    ha, fa = register_family(client, "adopt-x-a@example.ch")
    hb, _ = register_family(client, "adopt-x-b@example.ch")
    cid = _stash(fa, cand("Suppe", ["Karotten"]))
    assert _adopt(client, hb, candidate_id=cid).status_code == 404
    assert _adopt(client, ha, candidate_id=cid).status_code == 201


def test_unknown_or_expired_candidate_is_404(client, db_session):
    headers, _ = register_family(client, "adopt-404@example.ch")
    assert _adopt(client, headers, candidate_id=str(uuid.uuid4())).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"candidate_id": "nope"},
        {"candidate_id": str(uuid.uuid4()), "plan_date": "not-a-date", "meal_type": "lunch"},
        {"candidate_id": str(uuid.uuid4()), "plan_date": "2030-02-31", "meal_type": "lunch"},
        {"candidate_id": str(uuid.uuid4()), "plan_date": "2030-01-01", "meal_type": "brunch"},
        {"candidate_id": str(uuid.uuid4()), "plan_date": "2030-01-01"},
        {"candidate_id": str(uuid.uuid4()), "meal_type": "lunch"},
    ],
)
def test_invalid_adopt_input_is_422(body, client, db_session):
    headers, _ = register_family(client, f"adopt-422-{uuid.uuid4().hex[:6]}@example.ch")
    assert client.post("/recipes/discover/adopt", headers=headers, json=body).status_code == 422


def test_adopt_requires_authentication(client):
    assert client.post("/recipes/discover/adopt", json={"candidate_id": str(uuid.uuid4())}).status_code == 401


def test_child_cannot_adopt(client, db_session):
    headers, fam = register_family(client, "adopt-child@example.ch")
    child = add_member(db_session, fam, UserRole.CHILD, "Anna", 2016)
    cid = _stash(fam, cand("Suppe", ["Karotten"]))
    ch = pin_headers(client, fam, child)
    assert _adopt(client, ch, candidate_id=cid).status_code == 403


def test_adopt_sanitizes_persisted_data(client, db_session):
    headers, fam = register_family(client, "adopt-san@example.ch")
    existing = Ingredient(name="Tomaten")
    db_session.add(existing)
    db_session.commit()
    c = cand(
        "T" * 400,
        ["tomaten", "TOMATEN", "  ", "x" * 300, "Basilikum"],
        url="https://www.themealdb.com/meal/1",
        image="http://insecure.example/i.jpg",
    )
    c.source_name = "n" * 300
    resp = _adopt(client, headers, candidate_id=_stash(fam, c))
    assert resp.status_code == 201, resp.text
    recipe = db_session.get(Recipe, uuid.UUID(resp.json()["recipe_id"]))
    assert len(recipe.name) <= 150 and len(recipe.source_name) <= 100
    assert recipe.image_url is None  # http:// image dropped
    linked = db_session.scalars(
        select(Ingredient).join(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id)
    ).all()
    assert existing.id in {i.id for i in linked}  # reused case-insensitively
    assert all(len(i.name) <= 100 for i in linked)
    tomaten_rows = select(func.count()).select_from(Ingredient).where(func.lower(Ingredient.name) == "tomaten")
    assert db_session.scalar(tomaten_rows) == 1
    assert len(linked) <= 3  # tomaten (deduped), truncated long name, basilikum


@pytest.mark.parametrize(
    "url",
    [
        "http://www.chefkoch.de/rezepte/1",
        "https://evil.example/rezepte/1",
        "https://chefkoch.de.evil.example/1",
        "javascript:alert(1)",
        "https://www.chefkoch.de/" + "a" * 600,
    ],
)
def test_adopt_rejects_untrusted_source_url(url, client, db_session):
    headers, fam = register_family(client, f"adopt-url-{uuid.uuid4().hex[:6]}@example.ch")
    c = cand("Boese", ["Reis"], url=url)
    assert _adopt(client, headers, candidate_id=_stash(fam, c)).status_code == 422
    assert db_session.scalar(select(func.count()).select_from(Recipe).where(Recipe.family_id == fam)) == 0


def test_adopted_recipe_not_visible_to_other_family(client, db_session):
    ha, fa = register_family(client, "adopt-vis-a@example.ch")
    hb, _ = register_family(client, "adopt-vis-b@example.ch")
    rid = _adopt(client, ha, candidate_id=_stash(fa, cand("Suppe", ["Karotten"]))).json()["recipe_id"]
    assert rid not in {r["id"] for r in client.get("/recipes", headers=hb).json()}
