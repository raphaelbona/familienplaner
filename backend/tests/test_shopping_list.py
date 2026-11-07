"""Shopping list tests following TDD approach."""
from datetime import date

from sqlalchemy import select

from app.models.recipe import PantryItem
from app.models.user import User


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


def test_generate_shopping_list_includes_recipe_ingredients(client):
    """Generate a shopping list from meal plan and verify it includes recipe ingredients."""
    headers = _register_parent(client)

    # Create 2 ingredients
    tomato = client.post("/ingredients", headers=headers, json={"name": "Tomate"}).json()
    pasta = client.post("/ingredients", headers=headers, json={"name": "Pasta"}).json()

    # Create a recipe using both ingredients
    recipe = client.post(
        "/recipes",
        headers=headers,
        json={
            "name": "Pasta al Pomodoro",
            "instructions": "Alles zusammen kochen.",
            "ingredients": [
                {"ingredient_id": tomato["id"], "quantity": 500, "unit": "g"},
                {"ingredient_id": pasta["id"], "quantity": 400, "unit": "g"},
            ],
        },
    ).json()

    # Create a meal plan entry for today
    today = date.today().isoformat()
    client.post(
        "/meal-plan/entries",
        headers=headers,
        json={"date": today, "meal_type": "dinner", "recipe_id": recipe["id"]},
    )

    # Generate shopping list from meal plan
    shopping_list = client.post(
        "/shopping-lists/from-meal-plan",
        headers=headers,
        params={"from_date": today, "to_date": today},
    ).json()

    # Verify the shopping list has 2 items matching the recipe's ingredients
    assert shopping_list["status"] == "open"
    assert shopping_list["source"] == "meal_plan"
    assert len(shopping_list["items"]) == 2

    ingredient_ids = {item["ingredient_id"] for item in shopping_list["items"]}
    assert tomato["id"] in ingredient_ids
    assert pasta["id"] in ingredient_ids

    # Verify quantities are preserved
    tomato_item = next(i for i in shopping_list["items"] if i["ingredient_id"] == tomato["id"])
    assert tomato_item["quantity"] == 500
    assert tomato_item["unit"] == "g"
    assert tomato_item["ingredient_name"] == "Tomate"


def test_generate_shopping_list_excludes_pantry_items(client, db_session):
    """Pantry items should be excluded from the generated shopping list."""
    headers = _register_parent(client)

    # Get the current user's family_id
    admin = db_session.scalar(select(User).where(User.email == "koch@example.ch"))

    # Create 2 ingredients
    tomato = client.post("/ingredients", headers=headers, json={"name": "Tomate"}).json()
    pasta = client.post("/ingredients", headers=headers, json={"name": "Pasta"}).json()

    # Create a recipe using both ingredients
    recipe = client.post(
        "/recipes",
        headers=headers,
        json={
            "name": "Pasta al Pomodoro",
            "instructions": "Alles zusammen kochen.",
            "ingredients": [
                {"ingredient_id": tomato["id"], "quantity": 500, "unit": "g"},
                {"ingredient_id": pasta["id"], "quantity": 400, "unit": "g"},
            ],
        },
    ).json()

    # Add one ingredient (tomato) to the family's pantry
    db_session.add(PantryItem(family_id=admin.family_id, ingredient_id=tomato["id"]))
    db_session.commit()

    # Create a meal plan entry
    today = date.today().isoformat()
    client.post(
        "/meal-plan/entries",
        headers=headers,
        json={"date": today, "meal_type": "dinner", "recipe_id": recipe["id"]},
    )

    # Generate shopping list
    shopping_list = client.post(
        "/shopping-lists/from-meal-plan",
        headers=headers,
        params={"from_date": today, "to_date": today},
    ).json()

    # Should only have 1 item (pasta, not tomato which is in pantry)
    assert len(shopping_list["items"]) == 1
    assert shopping_list["items"][0]["ingredient_id"] == pasta["id"]


def test_list_shopping_lists_for_current_user(client):
    """GET /shopping-lists should return all lists for the authenticated user's family."""
    headers = _register_parent(client)

    # Create a shopping list
    tomato = client.post("/ingredients", headers=headers, json={"name": "Tomate"}).json()
    recipe = client.post(
        "/recipes",
        headers=headers,
        json={
            "name": "Pasta",
            "instructions": "Kochen.",
            "ingredients": [{"ingredient_id": tomato["id"]}],
        },
    ).json()

    today = date.today().isoformat()
    client.post(
        "/meal-plan/entries",
        headers=headers,
        json={"date": today, "meal_type": "dinner", "recipe_id": recipe["id"]},
    )

    shopping_list = client.post(
        "/shopping-lists/from-meal-plan",
        headers=headers,
        params={"from_date": today, "to_date": today},
    ).json()

    # List shopping lists
    result = client.get("/shopping-lists", headers=headers).json()

    assert isinstance(result, list)
    assert len(result) >= 1
    assert shopping_list["id"] in {item["id"] for item in result}


def test_list_shopping_lists_does_not_return_other_families(client):
    """Shopping lists should be filtered by family_id (tenancy check)."""
    headers1 = _register_parent(client, email="family1@example.ch")
    headers2 = _register_parent(client, email="family2@example.ch")

    # Create a shopping list for family 1
    tomato = client.post("/ingredients", headers=headers1, json={"name": "Tomate"}).json()
    recipe = client.post(
        "/recipes",
        headers=headers1,
        json={
            "name": "Pasta",
            "instructions": "Kochen.",
            "ingredients": [{"ingredient_id": tomato["id"]}],
        },
    ).json()

    today = date.today().isoformat()
    client.post(
        "/meal-plan/entries",
        headers=headers1,
        json={"date": today, "meal_type": "dinner", "recipe_id": recipe["id"]},
    )

    list1 = client.post(
        "/shopping-lists/from-meal-plan",
        headers=headers1,
        params={"from_date": today, "to_date": today},
    ).json()

    # Create a shopping list for family 2
    pasta = client.post("/ingredients", headers=headers2, json={"name": "Pasta"}).json()
    recipe2 = client.post(
        "/recipes",
        headers=headers2,
        json={
            "name": "Risotto",
            "instructions": "Rühren.",
            "ingredients": [{"ingredient_id": pasta["id"]}],
        },
    ).json()

    client.post(
        "/meal-plan/entries",
        headers=headers2,
        json={"date": today, "meal_type": "dinner", "recipe_id": recipe2["id"]},
    )

    list2 = client.post(
        "/shopping-lists/from-meal-plan",
        headers=headers2,
        params={"from_date": today, "to_date": today},
    ).json()

    # Family 1's list should not include family 2's list
    result1 = client.get("/shopping-lists", headers=headers1).json()
    result1_ids = {item["id"] for item in result1}

    assert list1["id"] in result1_ids
    assert list2["id"] not in result1_ids


def test_update_item_checked_sets_user(client, db_session):
    """PATCH should set checked_by when checking an item."""
    headers = _register_parent(client)

    # Get the current user
    admin = db_session.scalar(select(User).where(User.email == "koch@example.ch"))

    # Create a shopping list with an item
    tomato = client.post("/ingredients", headers=headers, json={"name": "Tomate"}).json()
    recipe = client.post(
        "/recipes",
        headers=headers,
        json={
            "name": "Pasta",
            "instructions": "Kochen.",
            "ingredients": [{"ingredient_id": tomato["id"]}],
        },
    ).json()

    today = date.today().isoformat()
    client.post(
        "/meal-plan/entries",
        headers=headers,
        json={"date": today, "meal_type": "dinner", "recipe_id": recipe["id"]},
    )

    shopping_list = client.post(
        "/shopping-lists/from-meal-plan",
        headers=headers,
        params={"from_date": today, "to_date": today},
    ).json()

    item_id = shopping_list["items"][0]["id"]
    list_id = shopping_list["id"]

    # Patch the item to check it
    response = client.patch(
        f"/shopping-lists/{list_id}/items/{item_id}",
        headers=headers,
        json={"checked": True},
    ).json()

    assert response["checked"] is True

    # Verify in DB that checked_by is set
    from app.models.shopping import ShoppingListItem

    db_item = db_session.get(ShoppingListItem, item_id)
    assert db_item.checked is True
    assert db_item.checked_by == admin.id


def test_update_item_tenancy_check(client):
    """PATCH on an item from a different family should return 404."""
    headers1 = _register_parent(client, email="family1@example.ch")
    headers2 = _register_parent(client, email="family2@example.ch")

    # Create a shopping list for family 1
    tomato = client.post("/ingredients", headers=headers1, json={"name": "Tomate"}).json()
    recipe = client.post(
        "/recipes",
        headers=headers1,
        json={
            "name": "Pasta",
            "instructions": "Kochen.",
            "ingredients": [{"ingredient_id": tomato["id"]}],
        },
    ).json()

    today = date.today().isoformat()
    client.post(
        "/meal-plan/entries",
        headers=headers1,
        json={"date": today, "meal_type": "dinner", "recipe_id": recipe["id"]},
    )

    shopping_list = client.post(
        "/shopping-lists/from-meal-plan",
        headers=headers1,
        params={"from_date": today, "to_date": today},
    ).json()

    item_id = shopping_list["items"][0]["id"]
    list_id = shopping_list["id"]

    # Try to patch as family 2
    response = client.patch(
        f"/shopping-lists/{list_id}/items/{item_id}",
        headers=headers2,
        json={"checked": True},
    )

    assert response.status_code == 404


def test_update_item_malformed_list_id_returns_422(client):
    """PATCH with a malformed (non-UUID) list_id should return 422."""
    headers = _register_parent(client)

    # Create a shopping list with an item
    tomato = client.post("/ingredients", headers=headers, json={"name": "Tomate"}).json()
    recipe = client.post(
        "/recipes",
        headers=headers,
        json={
            "name": "Pasta",
            "instructions": "Kochen.",
            "ingredients": [{"ingredient_id": tomato["id"]}],
        },
    ).json()

    today = date.today().isoformat()
    client.post(
        "/meal-plan/entries",
        headers=headers,
        json={"date": today, "meal_type": "dinner", "recipe_id": recipe["id"]},
    )

    shopping_list = client.post(
        "/shopping-lists/from-meal-plan",
        headers=headers,
        params={"from_date": today, "to_date": today},
    ).json()

    item_id = shopping_list["items"][0]["id"]

    # Patch with malformed list_id (not a UUID)
    response = client.patch(
        f"/shopping-lists/not-a-uuid/items/{item_id}",
        headers=headers,
        json={"checked": True},
    )

    assert response.status_code == 422


def test_update_item_negative_quantity_returns_422(client):
    """PATCH with negative quantity should return 422."""
    headers = _register_parent(client)

    # Create a shopping list with an item
    tomato = client.post("/ingredients", headers=headers, json={"name": "Tomate"}).json()
    recipe = client.post(
        "/recipes",
        headers=headers,
        json={
            "name": "Pasta",
            "instructions": "Kochen.",
            "ingredients": [{"ingredient_id": tomato["id"]}],
        },
    ).json()

    today = date.today().isoformat()
    client.post(
        "/meal-plan/entries",
        headers=headers,
        json={"date": today, "meal_type": "dinner", "recipe_id": recipe["id"]},
    )

    shopping_list = client.post(
        "/shopping-lists/from-meal-plan",
        headers=headers,
        params={"from_date": today, "to_date": today},
    ).json()

    item_id = shopping_list["items"][0]["id"]
    list_id = shopping_list["id"]

    # Patch with negative quantity
    response = client.patch(
        f"/shopping-lists/{list_id}/items/{item_id}",
        headers=headers,
        json={"quantity": -1},
    )

    assert response.status_code == 422


def test_update_item_zero_quantity_returns_422(client):
    """PATCH with zero quantity should return 422."""
    headers = _register_parent(client)

    # Create a shopping list with an item
    tomato = client.post("/ingredients", headers=headers, json={"name": "Tomate"}).json()
    recipe = client.post(
        "/recipes",
        headers=headers,
        json={
            "name": "Pasta",
            "instructions": "Kochen.",
            "ingredients": [{"ingredient_id": tomato["id"]}],
        },
    ).json()

    today = date.today().isoformat()
    client.post(
        "/meal-plan/entries",
        headers=headers,
        json={"date": today, "meal_type": "dinner", "recipe_id": recipe["id"]},
    )

    shopping_list = client.post(
        "/shopping-lists/from-meal-plan",
        headers=headers,
        params={"from_date": today, "to_date": today},
    ).json()

    item_id = shopping_list["items"][0]["id"]
    list_id = shopping_list["id"]

    # Patch with zero quantity
    response = client.patch(
        f"/shopping-lists/{list_id}/items/{item_id}",
        headers=headers,
        json={"quantity": 0},
    )

    assert response.status_code == 422
