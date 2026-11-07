import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import UUIDPk

if TYPE_CHECKING:
    from app.models.recipe import Recipe


class MealPlanEntry(Base, UUIDPk):
    __tablename__ = "meal_plan_entry"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE")
    )
    date: Mapped[date] = mapped_column(Date)
    meal_type: Mapped[str] = mapped_column(String(20))  # "breakfast" | "lunch" | "dinner"
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe.id", ondelete="CASCADE")
    )

    recipe: Mapped["Recipe"] = relationship()

    @property
    def recipe_name(self) -> str:
        return self.recipe.name
