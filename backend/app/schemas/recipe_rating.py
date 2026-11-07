"""Schemas for recipe ratings."""

import uuid
from datetime import date, timedelta

from pydantic import BaseModel, Field, field_validator

MAX_FUTURE_DAYS = 1  # timezone slack: the client's "today" may be ahead of the server's
MAX_PAST_DAYS = 365


class RecipeRatingCreate(BaseModel):
    """Create a recipe rating."""

    stars: int = Field(ge=1, le=5, description="Rating from 1-5")
    cooked_on: date = Field(description="Date recipe was cooked")
    user_id: uuid.UUID | None = Field(default=None, description="User ID (for parent rating on behalf of member)")

    @field_validator("cooked_on")
    @classmethod
    def _plausible_date(cls, value: date) -> date:
        today = date.today()
        if value > today + timedelta(days=MAX_FUTURE_DAYS):
            raise ValueError("cooked_on must not be more than one day in the future")
        if value < today - timedelta(days=MAX_PAST_DAYS):
            raise ValueError("cooked_on must not be older than one year")
        return value


class RecipeRatingRead(BaseModel):
    """Read a recipe rating."""

    id: str
    stars: int
    cooked_on: date

    model_config = {"from_attributes": True}
