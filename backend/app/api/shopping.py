import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.meal_plan import MealPlanEntry
from app.models.recipe import PantryItem, RecipeIngredient
from app.models.shopping import ShoppingList, ShoppingListItem
from app.models.user import User
from app.schemas.meal_plan import ShoppingListItemUpdate, ShoppingListRead

router = APIRouter(prefix="/shopping-lists", tags=["shopping-lists"])


@router.get("", response_model=list[ShoppingListRead])
def list_shopping_lists(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ShoppingList]:
    """Listet alle Einkaufslisten für die aktuelle Familie auf (neueste zuerst)."""
    return list(
        db.scalars(
            select(ShoppingList)
            .where(ShoppingList.family_id == current_user.family_id)
            .order_by(ShoppingList.created_at.desc())
        )
    )


@router.post("/from-meal-plan", response_model=ShoppingListRead, status_code=status.HTTP_201_CREATED)
def generate_from_meal_plan(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    from_date: date,
    to_date: date,
) -> ShoppingList:
    """Sammelt alle Zutaten der Rezepte im Zeitraum, die noch nicht in der Vorratskammer sind."""
    entries = db.scalars(
        select(MealPlanEntry).where(
            MealPlanEntry.family_id == current_user.family_id,
            MealPlanEntry.date >= from_date,
            MealPlanEntry.date <= to_date,
        )
    )
    recipe_ids = {entry.recipe_id for entry in entries}

    pantry_ids = {
        p.ingredient_id
        for p in db.scalars(select(PantryItem).where(PantryItem.family_id == current_user.family_id))
    }

    needed: dict = {}
    for link in db.scalars(select(RecipeIngredient).where(RecipeIngredient.recipe_id.in_(recipe_ids))):
        if link.ingredient_id in pantry_ids:
            continue
        existing = needed.get(link.ingredient_id)
        if existing:
            existing["quantity"] += link.quantity
        else:
            needed[link.ingredient_id] = {"quantity": link.quantity, "unit": link.unit}

    shopping_list = ShoppingList(family_id=current_user.family_id, source="meal_plan")
    db.add(shopping_list)
    db.flush()
    for ingredient_id, info in needed.items():
        db.add(
            ShoppingListItem(
                shopping_list_id=shopping_list.id,
                ingredient_id=ingredient_id,
                quantity=info["quantity"],
                unit=info["unit"],
            )
        )
    db.commit()
    db.refresh(shopping_list)
    return shopping_list


@router.patch("/{list_id}/items/{item_id}")
def update_item(
    list_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: ShoppingListItemUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    item = db.get(ShoppingListItem, item_id)
    if item is None or item.shopping_list_id != list_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eintrag nicht gefunden")

    # Verify the shopping list belongs to the current user's family
    shopping_list = db.get(ShoppingList, list_id)
    if shopping_list is None or shopping_list.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eintrag nicht gefunden")

    if payload.quantity is not None:
        item.quantity = payload.quantity
    if payload.checked is not None:
        item.checked = payload.checked
        item.checked_by = current_user.id if payload.checked else None
    db.commit()
    return {"id": str(item.id), "checked": item.checked, "quantity": item.quantity}
