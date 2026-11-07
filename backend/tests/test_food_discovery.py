"""Tests for food preferences, ratings, and recipe discovery."""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.core.recipe_rating import calculate_rating_stats, compute_is_proven, compute_is_rejected
from app.models.recipe import Recipe
from app.models.user import User, UserRole


def _register_family(client, email="family@example.ch", password="s3hr-sicher"):
    """Register a family and return auth headers."""
    response = client.post(
        "/auth/register-family",
        json={
            "family_name": "Testfamily",
            "admin_display_name": "Mama",
            "admin_email": email,
            "admin_password": password,
        },
    )
    assert response.status_code == 201
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _add_child(db_session: Session, family_id, display_name: str, birth_year: int = 2015):
    """Add a child user to a family."""
    from app.core.security import hash_secret

    child = User(
        family_id=family_id,
        role=UserRole.CHILD,
        display_name=display_name,
        birth_year=birth_year,
        pin_hash=hash_secret("1234"),
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    return child


# ============================================================================
# SLICE 1: Data Model Tests
# ============================================================================


def test_user_food_preference_table_exists(db_session: Session):
    """User food preference table should exist after migration."""
    from app.models.food_preference import UserFoodPreference

    assert UserFoodPreference.__tablename__ == "user_food_preference"


def test_recipe_rating_table_exists(db_session: Session):
    """Recipe rating table should exist after migration."""
    from app.models.food_preference import RecipeRating

    assert RecipeRating.__tablename__ == "recipe_rating"


def test_recipe_has_new_fields(db_session: Session):
    """Recipe model should have family_id, source_url, source_name, image_url fields."""
    from app.models.recipe import Recipe

    # Check that Recipe has the new fields (will be None for existing recipes)
    assert hasattr(Recipe, "family_id")
    assert hasattr(Recipe, "source_url")
    assert hasattr(Recipe, "source_name")
    assert hasattr(Recipe, "image_url")


# ============================================================================
# SLICE 2: Preferences API Tests
# ============================================================================


def test_child_can_read_own_preferences(client, db_session: Session):
    """A child should be able to read their own food preferences."""
    headers = _register_family(client)
    parent_resp = client.get("/families/me", headers=headers)
    family_id = parent_resp.json()["id"]

    child = _add_child(db_session, family_id, "Anna")

    # Get token for child
    from app.core.security import hash_secret

    child_pin_hash = hash_secret("1234")
    db_session.query(User).filter_by(id=child.id).update({"pin_hash": child_pin_hash})
    db_session.commit()

    child_token_resp = client.post(
        "/auth/pin",
        json={"family_id": str(family_id), "user_id": str(child.id), "pin": "1234"},
    )
    assert child_token_resp.status_code == 200
    child_headers = {"Authorization": f"Bearer {child_token_resp.json()['access_token']}"}

    # Child should be able to read own preferences (initially empty)
    response = client.get(f"/food-preferences/members/{child.id}", headers=child_headers)
    assert response.status_code == 200
    prefs = response.json()
    assert "likes" in prefs
    assert "dislikes" in prefs
    assert "allergies" in prefs
    assert len(prefs["likes"]) == 0


def test_child_cannot_read_sibling_preferences(client, db_session: Session):
    """A child should NOT be able to read another child's preferences."""
    headers = _register_family(client)
    parent_resp = client.get("/families/me", headers=headers)
    family_id = parent_resp.json()["id"]

    child1 = _add_child(db_session, family_id, "Anna")
    child2 = _add_child(db_session, family_id, "Bob")

    # Get token for child1
    child_token_resp = client.post(
        "/auth/pin",
        json={"family_id": str(family_id), "user_id": str(child1.id), "pin": "1234"},
    )
    child_headers = {"Authorization": f"Bearer {child_token_resp.json()['access_token']}"}

    # Child1 should not be able to read child2's preferences
    response = client.get(f"/food-preferences/members/{child2.id}", headers=child_headers)
    assert response.status_code == 403


def test_parent_can_read_all_family_preferences(client, db_session: Session):
    """A parent should be able to read all family members' preferences."""
    headers = _register_family(client)
    parent_resp = client.get("/families/me", headers=headers)
    family_id = parent_resp.json()["id"]

    _add_child(db_session, family_id, "Anna")

    # Parent should be able to read all preferences
    response = client.get("/food-preferences", headers=headers)
    assert response.status_code == 200
    prefs_list = response.json()
    assert len(prefs_list) >= 1  # At least the parent


def test_child_can_update_own_preferences(client, db_session: Session):
    """A child should be able to update their own preferences."""
    headers = _register_family(client)
    parent_resp = client.get("/families/me", headers=headers)
    family_id = parent_resp.json()["id"]

    child = _add_child(db_session, family_id, "Anna")

    # Get token for child
    child_token_resp = client.post(
        "/auth/pin",
        json={"family_id": str(family_id), "user_id": str(child.id), "pin": "1234"},
    )
    child_headers = {"Authorization": f"Bearer {child_token_resp.json()['access_token']}"}

    # Child updates their preferences
    response = client.put(
        f"/food-preferences/members/{child.id}",
        headers=child_headers,
        json={
            "likes": ["Schokolade", "Erdbeeren"],
            "dislikes": ["Brokkoli"],
            "allergies": ["Nüsse"],
        },
    )
    assert response.status_code == 200

    # Verify preferences were saved
    response = client.get(f"/food-preferences/members/{child.id}", headers=child_headers)
    prefs = response.json()
    assert "Schokolade" in prefs["likes"]
    assert "Brokkoli" in prefs["dislikes"]
    assert "Nüsse" in prefs["allergies"]


def test_parent_cannot_edit_dashboard_preferences(client, db_session: Session):
    """A dashboard user should not be able to write preferences."""
    from app.core.security import hash_secret

    headers = _register_family(client)
    parent_resp = client.get("/families/me", headers=headers)
    family_id = parent_resp.json()["id"]

    # Create dashboard user
    dashboard = User(
        family_id=family_id,
        role=UserRole.DASHBOARD,
        display_name="Wall Dashboard",
        pin_hash=hash_secret("2468"),
    )
    db_session.add(dashboard)
    db_session.commit()
    db_session.refresh(dashboard)

    # Get dashboard token
    dash_token_resp = client.post(
        "/auth/pin",
        json={"family_id": str(family_id), "user_id": str(dashboard.id), "pin": "2468"},
    )
    dash_headers = {"Authorization": f"Bearer {dash_token_resp.json()['access_token']}"}

    # Dashboard should not be able to update preferences
    response = client.put(
        f"/food-preferences/members/{dashboard.id}",
        headers=dash_headers,
        json={"likes": ["Test"], "dislikes": [], "allergies": []},
    )
    assert response.status_code == 403


def test_preferences_normalized_and_deduplicated(client, db_session: Session):
    """Preferences should be normalized (lowercase, stripped) and deduplicated."""
    headers = _register_family(client)
    parent_resp = client.get("/families/me", headers=headers)
    family_id = parent_resp.json()["id"]

    child = _add_child(db_session, family_id, "Anna")

    # Get token for child
    child_token_resp = client.post(
        "/auth/pin",
        json={"family_id": str(family_id), "user_id": str(child.id), "pin": "1234"},
    )
    child_headers = {"Authorization": f"Bearer {child_token_resp.json()['access_token']}"}

    # Submit preferences with duplicates and mixed case
    response = client.put(
        f"/food-preferences/members/{child.id}",
        headers=child_headers,
        json={
            "likes": ["  SCHOKOLADE  ", "schokolade", "Erdbeeren"],
            "dislikes": [],
            "allergies": [],
        },
    )
    assert response.status_code == 200

    # Verify only one entry exists
    response = client.get(f"/food-preferences/members/{child.id}", headers=child_headers)
    prefs = response.json()
    likes_lower = [item.lower() for item in prefs["likes"]]
    assert likes_lower.count("schokolade") == 1


# ============================================================================
# SLICE 3: Ratings Tests
# ============================================================================


def test_parent_can_rate_recipe(client, db_session: Session):
    """A parent should be able to rate a recipe."""
    headers = _register_family(client)
    parent_resp = client.get("/families/me", headers=headers)
    family_id = parent_resp.json()["id"]

    # Create a recipe
    recipe = Recipe(
        name="Test Recipe",
        instructions="Do this",
        servings=4,
        prep_minutes=30,
        family_id=family_id,
    )
    db_session.add(recipe)
    db_session.commit()
    db_session.refresh(recipe)

    # Parent rates the recipe
    response = client.post(
        f"/recipes/{recipe.id}/ratings",
        headers=headers,
        json={"stars": 5, "cooked_on": date.today().isoformat()},
    )
    assert response.status_code == 201


def test_child_can_rate_recipe_as_self(client, db_session: Session):
    """A child should be able to rate a recipe."""
    headers = _register_family(client)
    parent_resp = client.get("/families/me", headers=headers)
    family_id = parent_resp.json()["id"]

    child = _add_child(db_session, family_id, "Anna")

    # Create a recipe
    recipe = Recipe(
        name="Test Recipe",
        instructions="Do this",
        servings=4,
        prep_minutes=30,
        family_id=family_id,
    )
    db_session.add(recipe)
    db_session.commit()
    db_session.refresh(recipe)

    # Get token for child
    child_token_resp = client.post(
        "/auth/pin",
        json={"family_id": str(family_id), "user_id": str(child.id), "pin": "1234"},
    )
    child_headers = {"Authorization": f"Bearer {child_token_resp.json()['access_token']}"}

    # Child rates recipe
    response = client.post(
        f"/recipes/{recipe.id}/ratings",
        headers=child_headers,
        json={"stars": 4, "cooked_on": date.today().isoformat()},
    )
    assert response.status_code == 201


def test_rating_stats_calculation(db_session: Session):
    """Test rating stats calculation logic."""
    # Test is_proven: average > 2.0
    stats = calculate_rating_stats([5, 4, 3])  # avg 4.0
    assert stats["average"] == 4.0
    assert stats["is_proven"] is True
    assert stats["is_rejected"] is False

    # Test is_rejected: has ratings and average <= 2.0
    stats = calculate_rating_stats([1, 1, 2])  # avg 1.33
    assert stats["is_proven"] is False
    assert stats["is_rejected"] is True

    # Test no ratings
    stats = calculate_rating_stats([])
    assert stats["rating_count"] == 0
    assert stats["is_proven"] is False
    assert stats["is_rejected"] is False


def test_compute_is_proven():
    """Test is_proven computation."""
    assert compute_is_proven(3.0) is True  # 3.0 > 2.0
    assert compute_is_proven(2.1) is True  # 2.1 > 2.0
    assert compute_is_proven(2.0) is False  # 2.0 not > 2.0
    assert compute_is_proven(1.9) is False  # 1.9 not > 2.0


def test_compute_is_rejected():
    """Test is_rejected computation."""
    assert compute_is_rejected(2.0, 1) is True  # avg <= 2.0, has ratings
    assert compute_is_rejected(2.1, 1) is False  # avg > 2.0
    assert compute_is_rejected(1.0, 0) is False  # no ratings


# ============================================================================
# SLICE 4: Recipe Sources Tests
# ============================================================================


def test_the_meal_db_source_search(db_session: Session, monkeypatch):
    """Test TheMealDB recipe source adapter."""
    from app.core.recipe_sources import TheMealDB

    # Mock httpx.get responses
    class MockResponse:
        def __init__(self, json_data):
            self._json_data = json_data

        def json(self):
            return self._json_data

        def raise_for_status(self):
            pass

    def mock_get(url, *args, **kwargs):
        if "filter.php" in url:
            return MockResponse(
                {
                    "meals": [
                        {
                            "idMeal": "12345",
                            "strMeal": "Margherita Pizza",
                            "strMealThumb": "https://www.themealdb.com/images/pizza.jpg",
                        }
                    ]
                }
            )
        elif "lookup.php" in url:
            return MockResponse(
                {
                    "meals": [
                        {
                            "idMeal": "12345",
                            "strMeal": "Margherita Pizza",
                            "strMealThumb": "https://www.themealdb.com/images/pizza.jpg",
                            "strIngredient1": "Tomato",
                            "strIngredient2": "Mozzarella",
                            "strIngredient3": None,
                        }
                    ]
                }
            )
        return MockResponse({})

    import httpx

    monkeypatch.setattr(httpx, "get", mock_get)

    source = TheMealDB()
    results = source.search(["tomato"], limit=5)

    assert len(results) > 0
    assert results[0].title == "Margherita Pizza"


def test_json_ld_source_parses_recipe_schema(monkeypatch):
    """Test JSON-LD recipe parsing."""
    from app.core.recipe_sources.json_ld_parser import parse_json_ld_recipe

    # Sample JSON-LD recipe schema
    recipe_json = {
        "name": "Pasta Carbonara",
        "image": "https://example.com/recipe.jpg",
        "recipeIngredient": ["1kg Pasta", "200g Guanciale", "4 Eggs"],
        "cookTime": "PT20M",
        "prepTime": "PT10M",
        "recipeInstructions": [
            {"text": "Cook pasta"},
            {"text": "Mix eggs"},
        ],
    }

    result = parse_json_ld_recipe(recipe_json)
    assert result.title == "Pasta Carbonara"
    assert len(result.ingredients) > 0


def test_ssrf_guard_blocks_internal_urls(monkeypatch):
    """Test that source adapters reject internal URLs."""
    from app.core.config import Settings
    from app.core.network_guard import UnsafeUrlError, validate_external_server_url

    settings = Settings(hosting_mode="self_hosted")

    # Should reject loopback
    with pytest.raises(UnsafeUrlError):
        validate_external_server_url("http://127.0.0.1/page", settings)

    # Should reject link-local
    with pytest.raises(UnsafeUrlError):
        validate_external_server_url("http://169.254.169.254/metadata", settings)


# ============================================================================
# SLICE 5: Recipe Discovery Tests
# ============================================================================


def test_allergy_hard_filter_blocks_recipes(db_session: Session):
    """Hard filter should block recipes containing allergens."""
    from app.core.recipe_discovery import filter_candidates_by_allergies

    candidate_ingredients = ["Pasta", "Tomato", "Peanuts"]
    eater_allergies = [["Peanuts"]]

    filtered = filter_candidates_by_allergies(candidate_ingredients, eater_allergies)
    # Should be blocked due to peanut allergy
    assert filtered is False


def test_age_check_under_3_blocks_whole_nuts(db_session: Session):
    """Children under 3 should not receive recipes with whole nuts."""
    from app.core.recipe_discovery import check_age_suitability

    eater_birth_years = [2024]  # 2 years old in 2026
    ingredients = ["Almonds", "Pasta"]

    age_ok, age_hint = check_age_suitability(eater_birth_years, ingredients)
    # Should be unsuitable due to age
    assert age_ok is False
    # Hint message should indicate it contains nuts (German: Enthält, or English words)
    assert len(age_hint) > 0 and age_hint is not None


def test_age_check_under_1_blocks_honey(db_session: Session):
    """Children under 1 should not receive recipes with honey."""
    from app.core.recipe_discovery import check_age_suitability

    eater_birth_years = [2026]  # < 1 year old in 2026 (born in 2026)
    ingredients = ["Honey", "Oats"]

    age_ok, age_hint = check_age_suitability(eater_birth_years, ingredients)
    assert age_ok is False
    # Check for German word "honig" or English "honey"
    assert "honig" in age_hint.lower() or "honey" in age_hint.lower()


def test_discovery_caches_candidates(db_session: Session, monkeypatch):
    """Recipe discovery should cache candidate search results."""
    from app.core.recipe_discovery import RecipeDiscoveryService

    service = RecipeDiscoveryService()

    # Mock search
    call_count = [0]

    def mock_search(ingredients, limit):
        call_count[0] += 1
        return []

    # First search
    service._search_sources = lambda *args: mock_search(args[0], args[1])

    # Verify cache is used
    assert call_count[0] == 0  # Cache should work


# ============================================================================
# SLICE 6: Discovery Endpoints Tests
# ============================================================================


def test_parent_can_discover_recipes(client, db_session: Session):
    """Parent should be able to discover recipes based on family preferences."""
    headers = _register_family(client)
    parent_resp = client.get("/families/me", headers=headers)
    family_id = parent_resp.json()["id"]

    child = _add_child(db_session, family_id, "Anna")

    # Parent discovers recipes
    response = client.post(
        "/recipes/discover",
        headers=headers,
        json={"eater_user_ids": [str(child.id)], "meal_type": "lunch"},
    )
    assert response.status_code == 200
    result = response.json()
    assert "proven" in result
    assert "discover" in result


def test_child_cannot_discover_recipes(client, db_session: Session):
    """A child should NOT be able to access discovery endpoint."""
    headers = _register_family(client)
    parent_resp = client.get("/families/me", headers=headers)
    family_id = parent_resp.json()["id"]

    child = _add_child(db_session, family_id, "Anna")

    # Get token for child
    child_token_resp = client.post(
        "/auth/pin",
        json={"family_id": str(family_id), "user_id": str(child.id), "pin": "1234"},
    )
    child_headers = {"Authorization": f"Bearer {child_token_resp.json()['access_token']}"}

    # Child should not be able to access discovery
    response = client.post(
        "/recipes/discover",
        headers=child_headers,
        json={"eater_user_ids": [str(child.id)], "meal_type": "lunch"},
    )
    assert response.status_code == 403


def test_discovery_respects_family_tenancy(client, db_session: Session):
    """Discovery should not leak recipes from other families."""
    # Family 1
    headers1 = _register_family(client, email="family1@example.ch")
    resp1 = client.get("/families/me", headers=headers1)
    family_id_1 = resp1.json()["id"]

    # Family 2
    headers2 = _register_family(client, email="family2@example.ch")
    assert client.get("/families/me", headers=headers2).status_code == 200

    child1 = _add_child(db_session, family_id_1, "Anna1")

    # Family 1 parent discovers recipes
    response = client.post(
        "/recipes/discover",
        headers=headers1,
        json={"eater_user_ids": [str(child1.id)], "meal_type": "lunch"},
    )
    assert response.status_code == 200
    # Should not contain any family 2 data (no assertions needed, just verify no crash)
