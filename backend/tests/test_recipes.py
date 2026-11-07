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


def test_recipe_suggest_ranks_by_matching_ingredients(client):
    headers = _register_parent(client)

    tomato = client.post("/ingredients", headers=headers, json={"name": "Tomate"}).json()
    pasta = client.post("/ingredients", headers=headers, json={"name": "Pasta"}).json()
    basil = client.post("/ingredients", headers=headers, json={"name": "Basilikum"}).json()

    full_match = client.post(
        "/recipes",
        headers=headers,
        json={
            "name": "Pasta al Pomodoro",
            "instructions": "Alles zusammen kochen.",
            "ingredients": [
                {"ingredient_id": tomato["id"]},
                {"ingredient_id": pasta["id"]},
                {"ingredient_id": basil["id"]},
            ],
        },
    ).json()

    partial_match = client.post(
        "/recipes",
        headers=headers,
        json={
            "name": "Pasta Aglio e Olio",
            "instructions": "Knoblauch anbraten, Pasta dazu.",
            "ingredients": [{"ingredient_id": pasta["id"]}],
        },
    ).json()

    response = client.post(
        "/recipes/suggest",
        headers=headers,
        json={"available_ingredient_ids": [tomato["id"], pasta["id"]]},
    )
    assert response.status_code == 200
    suggestions = response.json()

    ids_in_order = [s["recipe"]["id"] for s in suggestions]
    assert ids_in_order[0] == full_match["id"]
    assert partial_match["id"] in ids_in_order

    full = next(s for s in suggestions if s["recipe"]["id"] == full_match["id"])
    assert full["matched_ingredients"] == 2
    assert full["missing_ingredients"] == 1


def test_list_recipes_returns_all_created_recipes(client):
    headers = _register_parent(client, email="list-recipes@example.ch")

    tomato = client.post("/ingredients", headers=headers, json={"name": "Tomate"}).json()
    client.post(
        "/recipes",
        headers=headers,
        json={
            "name": "Tomatensuppe",
            "instructions": "Kochen.",
            "ingredients": [{"ingredient_id": tomato["id"]}],
        },
    )

    response = client.get("/recipes", headers=headers)
    assert response.status_code == 200
    names = {r["name"] for r in response.json()}
    assert "Tomatensuppe" in names


def test_list_recipes_requires_authentication(client):
    response = client.get("/recipes")
    assert response.status_code == 401
