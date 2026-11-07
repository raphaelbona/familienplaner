"""Add series_id to calendar_event for recurring events

Revision ID: 9bba609356cf
Revises: f492a918b3a8
Create Date: 2026-09-13 17:00:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "9bba609356cf"
down_revision: str | None = "f492a918b3a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "calendar_event",
        sa.Column(
            "series_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calendar_event.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("calendar_event", "series_id")
