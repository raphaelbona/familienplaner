import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

MealType = Literal["breakfast", "lunch", "dinner"]


class MealPlanEntryCreate(BaseModel):
    date: date
    meal_type: MealType
    recipe_id: uuid.UUID


class MealPlanEntryRead(MealPlanEntryCreate):
    id: uuid.UUID
    recipe_name: str

    model_config = {"from_attributes": True}


class ShoppingListItemRead(BaseModel):
    id: uuid.UUID
    ingredient_id: uuid.UUID
    ingredient_name: str
    quantity: float
    unit: str
    checked: bool

    model_config = {"from_attributes": True}


class ShoppingListRead(BaseModel):
    id: uuid.UUID
    status: str
    source: str
    items: list[ShoppingListItemRead]

    model_config = {"from_attributes": True}


class ShoppingListItemUpdate(BaseModel):
    quantity: float | None = Field(None, gt=0)
    checked: bool | None = None
