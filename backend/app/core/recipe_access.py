"""Recipe visibility (tenancy) rules.

A recipe with ``family_id IS NULL`` is a global/legacy recipe visible to everybody.
A recipe with a ``family_id`` is visible only to that family.
"""

import uuid

from sqlalchemy import ColumnElement, or_
from sqlalchemy.orm import Session

from app.models.recipe import Recipe


def recipe_visible_to(family_id: uuid.UUID) -> ColumnElement[bool]:
    return or_(Recipe.family_id.is_(None), Recipe.family_id == family_id)


def get_visible_recipe(db: Session, recipe_id: uuid.UUID, family_id: uuid.UUID) -> Recipe | None:
    """Return the recipe if it is visible to ``family_id``, else None (never reveals existence)."""
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        return None
    if recipe.family_id is not None and recipe.family_id != family_id:
        return None
    return recipe
