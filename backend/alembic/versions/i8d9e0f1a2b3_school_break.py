"""school_break: school holidays and free days (manual or derived from all-day Google events)

Revision ID: i8d9e0f1a2b3
Revises: h7c8d9e0f1a2
Create Date: 2026-10-04 14:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "i8d9e0f1a2b3"
down_revision: str | None = "h7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "school_break",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=20), server_default="ferien", nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "child_user_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=20), server_default="manual", nullable=False),
        sa.Column("sync_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_calendar_id", sa.String(length=200), nullable=True),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_date >= start_date", name="ck_school_break_dates"),
        sa.ForeignKeyConstraint(["family_id"], ["family.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sync_account_id"], ["calendar_sync_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_school_break_family_id"), "school_break", ["family_id"], unique=False)
    op.create_index(
        "uq_school_break_google_event",
        "school_break",
        ["sync_account_id", "source_calendar_id", "external_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_school_break_google_event", table_name="school_break")
    op.drop_index(op.f("ix_school_break_family_id"), table_name="school_break")
    op.drop_table("school_break")
