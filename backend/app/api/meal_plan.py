from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.recipe_access import get_visible_recipe
from app.db.session import get_db
from app.models.meal_plan import MealPlanEntry
from app.models.user import User
from app.schemas.meal_plan import MealPlanEntryCreate, MealPlanEntryRead

router = APIRouter(prefix="/meal-plan", tags=["meal-plan"])


@router.get("/entries", response_model=list[MealPlanEntryRead])
def list_entries(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[MealPlanEntry]:
    stmt = select(MealPlanEntry).where(MealPlanEntry.family_id == current_user.family_id)
    if from_date:
        stmt = stmt.where(MealPlanEntry.date >= from_date)
    if to_date:
        stmt = stmt.where(MealPlanEntry.date <= to_date)
    return list(db.scalars(stmt.order_by(MealPlanEntry.date)))


@router.post("/entries", response_model=MealPlanEntryRead, status_code=status.HTTP_201_CREATED)
def create_entry(
    payload: MealPlanEntryCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MealPlanEntry:
    # A family may only plan recipes it can see (global or its own).
    if get_visible_recipe(db, payload.recipe_id, current_user.family_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recipe not found")
    entry = MealPlanEntry(family_id=current_user.family_id, **payload.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
