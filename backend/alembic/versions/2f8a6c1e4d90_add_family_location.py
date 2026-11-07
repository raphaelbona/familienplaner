"""Add latitude/longitude/location_name to family

Revision ID: 2f8a6c1e4d90
Revises: 9c4e7d21b8a3
Create Date: 2026-09-12 18:00:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2f8a6c1e4d90"
down_revision: str | None = "9c4e7d21b8a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("family", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("family", sa.Column("longitude", sa.Float(), nullable=True))
    op.add_column("family", sa.Column("location_name", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("family", "location_name")
    op.drop_column("family", "longitude")
    op.drop_column("family", "latitude")
