import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPk

if TYPE_CHECKING:
    from app.models.recipe import Ingredient


class ShoppingList(Base, UUIDPk, TimestampMixin):
    __tablename__ = "shopping_list"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(20), default="open")  # "open" | "done"
    source: Mapped[str] = mapped_column(String(20), default="manual")  # "manual" | "meal_plan"

    items: Mapped[list["ShoppingListItem"]] = relationship(
        back_populates="shopping_list", cascade="all, delete-orphan"
    )


class ShoppingListItem(Base, UUIDPk):
    __tablename__ = "shopping_list_item"

    shopping_list_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shopping_list.id", ondelete="CASCADE")
    )
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingredient.id", ondelete="CASCADE")
    )
    quantity: Mapped[float] = mapped_column(default=1.0)
    unit: Mapped[str] = mapped_column(String(20), default="stk")
    checked: Mapped[bool] = mapped_column(Boolean, default=False)
    checked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    shopping_list: Mapped["ShoppingList"] = relationship(back_populates="items")
    ingredient: Mapped["Ingredient"] = relationship()

    @property
    def ingredient_name(self) -> str:
        return self.ingredient.name
