"""Food preference models: likes, dislikes, allergies."""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPk


class FoodPreferenceKind(str, enum.Enum):
    """Kind of food preference."""

    LIKES = "likes"
    DISLIKES = "dislikes"
    ALLERGY = "allergy"


class UserFoodPreference(Base, UUIDPk):
    """Slice 1: Food preference (likes/dislikes/allergies) for a family member."""

    __tablename__ = "user_food_preference"
    __table_args__ = (UniqueConstraint("user_id", "ingredient_name", "kind", name="user_food_preference_unique"),)

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    ingredient_name: Mapped[str] = mapped_column(String(80))  # normalized lowercase, stripped
    kind: Mapped[FoodPreferenceKind] = mapped_column(Enum(FoodPreferenceKind, name="food_preference_kind"))


class RecipeRating(Base, UUIDPk):
    """Slice 3: Rating of a recipe by a family member."""

    __tablename__ = "recipe_rating"
    __table_args__ = (
        # one rating per member, recipe and day: a second post the same day updates the stars
        UniqueConstraint("recipe_id", "user_id", "cooked_on", name="recipe_rating_recipe_user_day_unique"),
    )

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE"), index=True
    )
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    stars: Mapped[int] = mapped_column(Integer, CheckConstraint("stars >= 1 AND stars <= 5"))
    cooked_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
