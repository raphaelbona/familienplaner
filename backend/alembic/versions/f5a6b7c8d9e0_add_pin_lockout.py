"""add PIN brute-force lockout columns (user) and per-device failure throttle (family_device)

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-09-20 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user", sa.Column("failed_pin_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("user", sa.Column("pin_locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user", sa.Column("pin_lock_level", sa.Integer(), server_default="0", nullable=False))
    op.add_column("family_device", sa.Column("failed_pin_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("family_device", sa.Column("pin_window_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("family_device", "pin_window_started_at")
    op.drop_column("family_device", "failed_pin_attempts")
    op.drop_column("user", "pin_lock_level")
    op.drop_column("user", "pin_locked_until")
    op.drop_column("user", "failed_pin_attempts")
