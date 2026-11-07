"""google calendar sync: account status, calendar selections, event provenance

Revision ID: h7c8d9e0f1a2
Revises: g6b7c8d9e0f1
Create Date: 2026-10-04 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "h7c8d9e0f1a2"
down_revision: str | None = "g6b7c8d9e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("calendar_sync_account", sa.Column("google_email", sa.String(length=200), nullable=True))
    op.add_column(
        "calendar_sync_account",
        sa.Column("status", sa.String(length=20), server_default="ok", nullable=False),
    )
    op.add_column("calendar_sync_account", sa.Column("last_error", sa.String(length=300), nullable=True))

    op.create_table(
        "google_calendar_selection",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("google_calendar_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "owner_user_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["calendar_sync_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "google_calendar_id", name="uq_google_calendar_selection_account_calendar"),
    )
    op.create_index(
        op.f("ix_google_calendar_selection_account_id"), "google_calendar_selection", ["account_id"], unique=False
    )

    op.add_column("calendar_event", sa.Column("sync_account_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("calendar_event", sa.Column("source_calendar_id", sa.String(length=200), nullable=True))
    op.add_column(
        "calendar_event",
        sa.Column("is_private", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index(op.f("ix_calendar_event_sync_account_id"), "calendar_event", ["sync_account_id"], unique=False)
    op.create_foreign_key(
        "calendar_event_sync_account_id_fkey",
        "calendar_event",
        "calendar_sync_account",
        ["sync_account_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # The old importer wrote ownerless google rows that were never removed and cannot be attributed to an
    # account or calendar. They are derived data: drop them, the next sync recreates them with provenance.
    op.execute("DELETE FROM calendar_event WHERE source = 'google'")
    # Accounts connected before this migration keep importing their primary calendar (what the old worker did).
    op.execute(
        "INSERT INTO google_calendar_selection (id, account_id, google_calendar_id, name) "
        "SELECT gen_random_uuid(), id, 'primary', 'Primärkalender' "
        "FROM calendar_sync_account WHERE provider = 'google'"
    )


def downgrade() -> None:
    op.drop_constraint("calendar_event_sync_account_id_fkey", "calendar_event", type_="foreignkey")
    op.drop_index(op.f("ix_calendar_event_sync_account_id"), table_name="calendar_event")
    op.drop_column("calendar_event", "is_private")
    op.drop_column("calendar_event", "source_calendar_id")
    op.drop_column("calendar_event", "sync_account_id")
    op.drop_index(op.f("ix_google_calendar_selection_account_id"), table_name="google_calendar_selection")
    op.drop_table("google_calendar_selection")
    op.drop_column("calendar_sync_account", "last_error")
    op.drop_column("calendar_sync_account", "status")
    op.drop_column("calendar_sync_account", "google_email")
