"""add food preferences and ratings

Revision ID: b1a2c3d4e5f6
Revises: a91c2d4e6f08
Create Date: 2026-09-19 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b1a2c3d4e5f6"
down_revision: str | None = "a91c2d4e6f08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create food_preference_kind enum
    op.execute("CREATE TYPE food_preference_kind AS ENUM ('LIKES', 'DISLIKES', 'ALLERGY')")

    # Add new columns to recipe table
    op.add_column(
        "recipe",
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "recipe",
        sa.Column("source_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "recipe",
        sa.Column("source_name", sa.String(100), nullable=True),
    )
    op.add_column(
        "recipe",
        sa.Column("image_url", sa.String(500), nullable=True),
    )
    op.create_foreign_key(
        "recipe_family_id_fk",
        "recipe",
        "family",
        ["family_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Create user_food_preference table
    op.create_table(
        "user_food_preference",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingredient_name", sa.String(80), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM("LIKES", "DISLIKES", "ALLERGY", name="food_preference_kind", create_type=False),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["family_id"], ["family.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "ingredient_name", "kind", name="user_food_preference_unique"),
    )
    op.create_index("ix_user_food_preference_family_id", "user_food_preference", ["family_id"])
    op.create_index("ix_user_food_preference_user_id", "user_food_preference", ["user_id"])

    # Create recipe_rating table
    op.create_table(
        "recipe_rating",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("cooked_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("stars >= 1 AND stars <= 5", name="recipe_rating_stars_check"),
        sa.ForeignKeyConstraint(["family_id"], ["family.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipe.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recipe_rating_family_id", "recipe_rating", ["family_id"])
    op.create_index("ix_recipe_rating_recipe_id", "recipe_rating", ["recipe_id"])
    op.create_index("ix_recipe_rating_user_id", "recipe_rating", ["user_id"])


def downgrade() -> None:
    # Drop tables
    op.drop_table("recipe_rating")
    op.drop_table("user_food_preference")

    # Drop foreign key and columns from recipe
    op.drop_constraint("recipe_family_id_fk", "recipe", type_="foreignkey")
    op.drop_column("recipe", "image_url")
    op.drop_column("recipe", "source_name")
    op.drop_column("recipe", "source_url")
    op.drop_column("recipe", "family_id")

    # Drop enum type
    op.execute("DROP TYPE food_preference_kind")
