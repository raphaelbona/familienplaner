import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPk


class PointsLedgerEntry(Base, UUIDPk):
    """Append-only. A user's points balance is SUM(delta)."""

    __tablename__ = "points_ledger_entry"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE")
    )
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(40))  # "task_completed" | "reward_redeemed" | "manual_adjustment"
    task_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_instance.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class RewardCatalogItem(Base, UUIDPk):
    __tablename__ = "reward_catalog_item"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(150))
    points_cost: Mapped[int] = mapped_column(Integer)
    icon: Mapped[str | None] = mapped_column(String(8), nullable=True)


class EarningsLedgerEntry(Base, UUIDPk):
    """Append-only CHF ledger. status: pending_approval -> approved -> paid_out."""

    __tablename__ = "earnings_ledger_entry"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE")
    )
    amount_chf: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending_approval")
    task_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_instance.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
