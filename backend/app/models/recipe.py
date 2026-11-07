import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPk


class Ingredient(Base, UUIDPk):
    __tablename__ = "ingredient"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    default_unit: Mapped[str] = mapped_column(String(20), default="stk")


class Recipe(Base, UUIDPk, TimestampMixin):
    __tablename__ = "recipe"
    __table_args__ = (
        # one family recipe per source URL: concurrent adopts of the same candidate cannot duplicate it
        Index(
            "recipe_family_source_url_unique",
            "family_id",
            "source_url",
            unique=True,
            postgresql_where=text("source_url IS NOT NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(150))
    instructions: Mapped[str] = mapped_column(String(4000))
    servings: Mapped[int] = mapped_column(Integer, default=4)
    prep_minutes: Mapped[int] = mapped_column(Integer, default=30)
    image_asset_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Slice 1: family_id (null = legacy global recipes)
    family_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE"), nullable=True
    )
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredient"

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe.id", ondelete="CASCADE"), primary_key=True
    )
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingredient.id", ondelete="CASCADE"), primary_key=True
    )
    quantity: Mapped[float] = mapped_column(default=1.0)
    unit: Mapped[str] = mapped_column(String(20), default="stk")


class PantryItem(Base):
    """A family's on-hand ingredients ('Wunschzutaten') used to match recipes."""

    __tablename__ = "pantry_item"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE"), primary_key=True
    )
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingredient.id", ondelete="CASCADE"), primary_key=True
    )
