"""day phases (Tagesphasen): day_phase, task_template_phase, carry_over, task_instance.phase_id, status expired

Revision ID: j9e0f1a2b3c4
Revises: i8d9e0f1a2b3
Create Date: 2026-10-30 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "j9e0f1a2b3c4"
down_revision: str | None = "i8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The enum labels of this schema are the Python member names (initial schema: 'OPEN', ...), so 'EXPIRED'.
    # ALTER TYPE ... ADD VALUE cannot run inside the migration's transaction block on older Postgres versions.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'EXPIRED'")

    op.create_table(
        "day_phase",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("icon", sa.String(length=8), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("day_type", sa.String(length=10), server_default="both", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_time > start_time", name="ck_day_phase_times"),
        sa.CheckConstraint("day_type IN ('school', 'free', 'both')", name="ck_day_phase_day_type"),
        sa.ForeignKeyConstraint(["family_id"], ["family.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_day_phase_family_id"), "day_phase", ["family_id"], unique=False)

    op.create_table(
        "task_template_phase",
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phase_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["task_template.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["phase_id"], ["day_phase.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("template_id", "phase_id"),
    )

    op.add_column(
        "task_template",
        sa.Column("carry_over", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    op.add_column("task_instance", sa.Column("phase_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(op.f("ix_task_instance_phase_id"), "task_instance", ["phase_id"], unique=False)
    op.create_foreign_key(
        "fk_task_instance_phase_id", "task_instance", "day_phase", ["phase_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_task_instance_phase_id", "task_instance", type_="foreignkey")
    op.drop_index(op.f("ix_task_instance_phase_id"), table_name="task_instance")
    op.drop_column("task_instance", "phase_id")
    op.drop_column("task_template", "carry_over")
    op.drop_table("task_template_phase")
    op.drop_index(op.f("ix_day_phase_family_id"), table_name="day_phase")
    op.drop_table("day_phase")
    # PostgreSQL cannot remove an enum value without rebuilding the columns that use it: the 'EXPIRED' label of
    # task_status stays (harmless, nothing deleted). Rows with that status stay readable for the old code only
    # if it knows the label, so downgrade just does not fail.
