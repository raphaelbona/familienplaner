"""Defect 1: recipes with a family_id must only be visible to that family."""

from datetime import date

from tests.food_helpers import admin_user, make_recipe, register_family


def _create_recipe_via_api(client, headers, name):
    ing = client.post("/ingredients", headers=headers, json={"name": f"Zutat-{name}"}).json()
    resp = client.post(
        "/recipes",
        headers=headers,
        json={"name": name, "instructions": "x", "ingredients": [{"ingredient_id": ing["id"]}]},
    )
    assert resp.status_code == 201
    return resp.json(), ing


def test_list_recipes_hides_other_familys_recipes(client, db_session):
    ha, fa = register_family(client, "ten-a@example.ch")
    hb, fb = register_family(client, "ten-b@example.ch")
    make_recipe(db_session, fa, "Geheim A")
    make_recipe(db_session, fb, "Offen B")
    make_recipe(db_session, None, "Global Klassiker")

    names_a = {r["name"] for r in client.get("/recipes", headers=ha).json()}
    names_b = {r["name"] for r in client.get("/recipes", headers=hb).json()}
    assert "Geheim A" in names_a and "Global Klassiker" in names_a
    assert "Offen B" not in names_a
    assert "Offen B" in names_b and "Geheim A" not in names_b


def test_created_recipe_belongs_to_creating_family(client, db_session):
    ha, _ = register_family(client, "ten-c@example.ch")
    hb, _ = register_family(client, "ten-d@example.ch")
    _create_recipe_via_api(client, ha, "Nur fuer A")
    assert "Nur fuer A" in {r["name"] for r in client.get("/recipes", headers=ha).json()}
    assert "Nur fuer A" not in {r["name"] for r in client.get("/recipes", headers=hb).json()}


def test_suggest_only_uses_visible_recipes(client, db_session):
    ha, _ = register_family(client, "ten-e@example.ch")
    hb, _ = register_family(client, "ten-f@example.ch")
    _, ing = _create_recipe_via_api(client, ha, "Suggest A")
    resp = client.post("/recipes/suggest", headers=hb, json={"available_ingredient_ids": [ing["id"]]})
    assert resp.status_code == 200
    assert resp.json() == []
    resp = client.post("/recipes/suggest", headers=ha, json={"available_ingredient_ids": [ing["id"]]})
    assert [s["recipe"]["name"] for s in resp.json()] == ["Suggest A"]


def test_other_family_cannot_rate_or_summarise_recipe(client, db_session):
    ha, fa = register_family(client, "ten-g@example.ch")
    hb, _ = register_family(client, "ten-h@example.ch")
    recipe = make_recipe(db_session, fa, "Rate A")
    body = {"stars": 5, "cooked_on": date.today().isoformat()}
    assert client.post(f"/recipes/{recipe.id}/ratings", headers=hb, json=body).status_code == 404
    assert client.get(f"/recipes/{recipe.id}/ratings-summary", headers=hb).status_code == 404
    assert client.post(f"/recipes/{recipe.id}/ratings", headers=ha, json=body).status_code == 201


def test_global_recipe_can_be_rated_by_any_family(client, db_session):
    hb, _ = register_family(client, "ten-i@example.ch")
    recipe = make_recipe(db_session, None, "Global Rate")
    body = {"stars": 4, "cooked_on": date.today().isoformat()}
    assert client.post(f"/recipes/{recipe.id}/ratings", headers=hb, json=body).status_code == 201


def test_rating_invalid_ids_are_422_not_500(client, db_session):
    ha, fa = register_family(client, "ten-j@example.ch")
    recipe = make_recipe(db_session, fa, "Rate J")
    body = {"stars": 4, "cooked_on": date.today().isoformat()}
    assert client.post("/recipes/not-a-uuid/ratings", headers=ha, json=body).status_code == 422
    bad = {**body, "user_id": "nope"}
    assert client.post(f"/recipes/{recipe.id}/ratings", headers=ha, json=bad).status_code == 422


def test_cannot_plan_other_familys_recipe(client, db_session):
    ha, fa = register_family(client, "ten-k@example.ch")
    hb, _ = register_family(client, "ten-l@example.ch")
    recipe = make_recipe(db_session, fa, "Plan A")
    body = {"date": date.today().isoformat(), "meal_type": "dinner", "recipe_id": str(recipe.id)}
    assert client.post("/meal-plan/entries", headers=hb, json=body).status_code == 404
    assert client.post("/meal-plan/entries", headers=ha, json=body).status_code == 201
    assert client.get("/meal-plan/entries", headers=hb).json() == []


def test_can_plan_global_recipe(client, db_session):
    hb, _ = register_family(client, "ten-m@example.ch")
    recipe = make_recipe(db_session, None, "Plan Global")
    body = {"date": date.today().isoformat(), "meal_type": "lunch", "recipe_id": str(recipe.id)}
    assert client.post("/meal-plan/entries", headers=hb, json=body).status_code == 201


def test_meal_plan_rejects_invalid_meal_type(client, db_session):
    ha, fa = register_family(client, "ten-n@example.ch")
    recipe = make_recipe(db_session, fa, "Plan N")
    body = {"date": date.today().isoformat(), "meal_type": "brunch", "recipe_id": str(recipe.id)}
    assert client.post("/meal-plan/entries", headers=ha, json=body).status_code == 422


def test_admin_helper_sanity(client, db_session):
    _, fa = register_family(client, "ten-o@example.ch")
    assert admin_user(db_session, fa) is not None
