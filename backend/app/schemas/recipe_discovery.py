"""Request/response schemas for recipe discovery and adoption."""

import uuid
from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.schemas.meal_plan import MealType

ExtraIngredient = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class DiscoverRequest(BaseModel):
    eater_user_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    meal_type: MealType = "lunch"
    extra_ingredients: list[ExtraIngredient] = Field(default_factory=list, max_length=10)


class AdoptRequest(BaseModel):
    candidate_id: uuid.UUID
    plan_date: date | None = None
    meal_type: MealType | None = None

    @model_validator(mode="after")
    def _plan_fields_go_together(self) -> "AdoptRequest":
        if (self.plan_date is None) != (self.meal_type is None):
            raise ValueError("plan_date and meal_type must be given together")
        return self


class AdoptResponse(BaseModel):
    recipe_id: uuid.UUID
    created: bool
    meal_plan_entry_id: uuid.UUID | None = None
