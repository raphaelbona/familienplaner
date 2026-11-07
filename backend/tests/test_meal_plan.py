from datetime import date


def _register_parent(client, email="koch@example.ch"):
    response = client.post(
        "/auth/register-family",
        json={
            "family_name": "Muster",
            "admin_display_name": "Elternteil",
            "admin_email": email,
            "admin_password": "s3hr-sicher",
        },
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_meal_plan_entry_includes_recipe_name(client):
    headers = _register_parent(client, email="mealplan1@example.ch")

    tomato = client.post("/ingredients", headers=headers, json={"name": "Tomate"}).json()
    recipe = client.post(
        "/recipes",
        headers=headers,
        json={
            "name": "Tomatensuppe",
            "instructions": "Kochen.",
            "ingredients": [{"ingredient_id": tomato["id"]}],
        },
    ).json()

    entry = client.post(
        "/meal-plan/entries",
        headers=headers,
        json={"date": date.today().isoformat(), "meal_type": "dinner", "recipe_id": recipe["id"]},
    )
    assert entry.status_code == 201
    assert entry.json()["recipe_name"] == "Tomatensuppe"

    listed = client.get("/meal-plan/entries", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["recipe_name"] == "Tomatensuppe"
