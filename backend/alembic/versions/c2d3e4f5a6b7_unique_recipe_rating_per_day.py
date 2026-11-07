"""one recipe rating per member, recipe and day

Revision ID: c2d3e4f5a6b7
Revises: b1a2c3d4e5f6
Create Date: 2026-09-19 14:00:00.000000
"""
from collections.abc import Sequence

from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1a2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "recipe_rating_recipe_user_day_unique"


def upgrade() -> None:
    # Defensive dedupe: keep only the newest row per (recipe, user, day), otherwise the
    # constraint below could not be created on databases that already collected spam ratings.
    op.execute(
        """
        DELETE FROM recipe_rating
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY recipe_id, user_id, cooked_on
                           ORDER BY created_at DESC, id DESC
                       ) AS rn
                FROM recipe_rating
            ) ranked
            WHERE ranked.rn > 1
        )
        """
    )
    op.create_unique_constraint(CONSTRAINT, "recipe_rating", ["recipe_id", "user_id", "cooked_on"])


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "recipe_rating", type_="unique")
