import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPk


class SchoolBreak(Base, UUIDPk, TimestampMixin):
    """School holidays / free days of a family: the weekly timetable does not apply on these dates.

    Entered by a parent (source "manual") or derived from an all-day Google event (source "google", see
    app.core.google_sync). ``end_date`` is inclusive. ``child_user_ids`` empty = applies to every child.
    """

    __tablename__ = "school_break"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_school_break_dates"),
        # A Google event maps to at most one row per account and calendar (NULLs of manual rows never collide).
        Index("uq_school_break_google_event", "sync_account_id", "source_calendar_id", "external_id", unique=True),
    )

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(100))
    # "ferien" | "schulfrei"
    kind: Mapped[str] = mapped_column(String(20), default="ferien", server_default="ferien")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    # Empty = all children. Validated against the family on write; ids of removed children are simply ignored.
    child_user_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list, server_default=text("'{}'::uuid[]")
    )

    # "manual" | "google"
    source: Mapped[str] = mapped_column(String(20), default="manual", server_default="manual")
    sync_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calendar_sync_account.id", ondelete="CASCADE"), nullable=True
    )
    source_calendar_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
