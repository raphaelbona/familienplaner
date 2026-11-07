"""Shared helpers for the food-preference / recipe-discovery tests."""

import zlib
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.recipe_sources import RecipeCandidate
from app.core.security import hash_secret
from app.models.food_preference import FoodPreferenceKind, RecipeRating, UserFoodPreference
from app.models.recipe import Ingredient, Recipe, RecipeIngredient
from app.models.user import User, UserRole


def register_family(client, email: str, name: str = "Familie"):
    """Register a family. Returns (auth headers, family_id UUID)."""
    resp = client.post(
        "/auth/register-family",
        json={
            "family_name": name,
            "admin_display_name": "Mama",
            "admin_email": email,
            "admin_password": "s3hr-sicher",
        },
    )
    assert resp.status_code == 201, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    me = client.get("/families/me", headers=headers).json()
    return headers, UUID(me["id"])


def add_member(db: Session, family_id, role: UserRole, name: str, birth_year: int | None = None, pin="1234"):
    user = User(
        family_id=family_id,
        role=role,
        display_name=name,
        birth_year=birth_year,
        pin_hash=hash_secret(pin),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def pin_headers(client, family_id, user, pin="1234"):
    resp = client.post(
        "/auth/pin",
        json={"family_id": str(family_id), "user_id": str(user.id), "pin": pin},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def set_prefs(db: Session, family_id, user, likes=(), dislikes=(), allergies=()):
    for kind, items in (
        (FoodPreferenceKind.LIKES, likes),
        (FoodPreferenceKind.DISLIKES, dislikes),
        (FoodPreferenceKind.ALLERGY, allergies),
    ):
        for name in items:
            db.add(UserFoodPreference(family_id=family_id, user_id=user.id, ingredient_name=name, kind=kind))
    db.commit()


def make_recipe(db: Session, family_id, name: str, ingredient_names=(), source_url=None) -> Recipe:
    recipe = Recipe(
        family_id=family_id,
        name=name,
        instructions="kochen",
        servings=4,
        prep_minutes=20,
        source_url=source_url,
    )
    db.add(recipe)
    db.flush()
    for ing_name in ingredient_names:
        ing = db.query(Ingredient).filter(Ingredient.name == ing_name).first()
        if ing is None:
            ing = Ingredient(name=ing_name)
            db.add(ing)
            db.flush()
        db.add(RecipeIngredient(recipe_id=recipe.id, ingredient_id=ing.id))
    db.commit()
    db.refresh(recipe)
    return recipe


def rate(db: Session, family_id, recipe, user, stars: int):
    db.add(RecipeRating(family_id=family_id, recipe_id=recipe.id, user_id=user.id, stars=stars, cooked_on=date.today()))
    db.commit()


def _recipe_id(slug: str) -> int:
    """Stable, realistic 16-digit Chefkoch-style recipe id."""
    return 1274521232800000 + zlib.crc32(slug.encode()) % 100000


def cand(title, ingredients, url=None, prep=25, image=None) -> RecipeCandidate:
    slug = title.lower().replace(" ", "-")
    return RecipeCandidate(
        title=title,
        image_url=image,
        ingredients=list(ingredients),
        prep_minutes=prep,
        servings=4,
        source_url=url or f"https://www.chefkoch.de/rezepte/{_recipe_id(slug)}/{slug}.html",
        source_name="chefkoch.de",
    )


class FakeSource:
    """Deterministic recipe source; records the queries it receives."""

    def __init__(self, candidates=(), error: Exception | None = None):
        self.candidates = list(candidates)
        self.error = error
        self.calls: list[list[str]] = []

    def search(self, ingredients, limit=5):
        self.calls.append(list(ingredients))
        if self.error:
            raise self.error
        return list(self.candidates)[:limit]


def admin_user(db: Session, family_id) -> User:
    return (
        db.query(User)
        .filter(User.family_id == family_id, User.role == UserRole.PARENT_ADMIN)
        .order_by(User.created_at)
        .first()
    )
