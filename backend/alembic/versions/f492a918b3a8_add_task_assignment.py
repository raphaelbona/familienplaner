"""Add task_assignment table for recurring (daily/weekly) task due dates

Revision ID: f492a918b3a8
Revises: 2f8a6c1e4d90
Create Date: 2026-09-13 14:00:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f492a918b3a8"
down_revision: str | None = "2f8a6c1e4d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_assignment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "family_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("family.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("task_template.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assignee_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recurrence_rule", sa.String(length=20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
    )
    op.add_column(
        "task_instance",
        sa.Column(
            "assignment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("task_assignment.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("task_instance", "assignment_id")
    op.drop_table("task_assignment")
