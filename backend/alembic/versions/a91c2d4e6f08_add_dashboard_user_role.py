"""add dashboard user role

Revision ID: a91c2d4e6f08
Revises: d8f2b6a1c9e4
Create Date: 2026-09-16 14:20:00.000000
"""
from collections.abc import Sequence

from alembic import op


revision: str = "a91c2d4e6f08"
down_revision: str | None = "d8f2b6a1c9e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'DASHBOARD'")


def downgrade() -> None:
    # PostgreSQL cannot safely remove enum values without rebuilding dependent columns.
    pass
