"""Add icon to task_template and reward_catalog_item

Revision ID: d8f2b6a1c9e4
Revises: c4e8a1f3b7d2
Create Date: 2026-09-14 10:00:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d8f2b6a1c9e4"
down_revision: str | None = "c4e8a1f3b7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_template", sa.Column("icon", sa.String(length=8), nullable=True))
    op.add_column("reward_catalog_item", sa.Column("icon", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("reward_catalog_item", "icon")
    op.drop_column("task_template", "icon")
