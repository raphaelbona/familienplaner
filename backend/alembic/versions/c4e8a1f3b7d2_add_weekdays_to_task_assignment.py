"""Add weekdays to task_assignment for N-times-per-week recurrence

Revision ID: c4e8a1f3b7d2
Revises: 9bba609356cf
Create Date: 2026-09-14 09:00:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c4e8a1f3b7d2"
down_revision: str | None = "9bba609356cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_assignment", sa.Column("weekdays", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("task_assignment", "weekdays")
