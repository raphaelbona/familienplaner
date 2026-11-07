"""one recipe per source_url and family

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-09-19 16:00:00.000000
"""
from collections.abc import Sequence

from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "recipe_family_source_url_unique"


def upgrade() -> None:
    # Defensive dedupe: the oldest recipe per (family, source_url) keeps its link, newer duplicates lose only
    # the source_url (nothing is deleted, so meal plans and ratings pointing at them stay valid).
    op.execute(
        """
        UPDATE recipe SET source_url = NULL
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY family_id, source_url
                           ORDER BY created_at ASC, id ASC
                       ) AS rn
                FROM recipe
                WHERE source_url IS NOT NULL AND family_id IS NOT NULL
            ) ranked
            WHERE ranked.rn > 1
        )
        """
    )
    op.create_index(
        INDEX,
        "recipe",
        ["family_id", "source_url"],
        unique=True,
        postgresql_where="source_url IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="recipe")
