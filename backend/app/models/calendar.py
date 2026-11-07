import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, false, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPk


class CalendarEvent(Base, UUIDPk, TimestampMixin):
    __tablename__ = "calendar_event"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE")
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200))
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool] = mapped_column(default=False)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # "native" | "google" | "caldav"
    source: Mapped[str] = mapped_column(String(20), default="native")
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recurrence_rule: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Set on generated occurrences, pointing back to the event that started the series
    # (which carries the recurrence_rule itself). NULL on one-off events and on series roots.
    series_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calendar_event.id", ondelete="CASCADE"), nullable=True
    )

    # Google import (see app.core.google_sync). One Google event yields one row per assigned member, so the
    # upsert key of an imported event is (family_id, source, external_id, source_calendar_id, owner_user_id).
    # NULL on native events; SET NULL when the account is disconnected but the imported events are kept.
    sync_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calendar_sync_account.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_calendar_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # True when Google marks the event private/confidential: non-parents only ever see "Belegt".
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class CalendarSyncAccount(Base, UUIDPk, TimestampMixin):
    __tablename__ = "calendar_sync_account"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_calendar_sync_account_user_provider"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(20))  # "google" | "caldav"
    oauth_token_encrypted: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    calendar_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Google only: the account's primary calendar id (read from calendarList, no extra OAuth scope).
    google_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # "ok" | "needs_reauth" | "error"
    status: Mapped[str] = mapped_column(String(20), default="ok", server_default="ok")
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)


class GoogleCalendarSelection(Base, UUIDPk):
    """A Google calendar the parent chose to import, with the members its events default to."""

    __tablename__ = "google_calendar_selection"
    __table_args__ = (
        UniqueConstraint("account_id", "google_calendar_id", name="uq_google_calendar_selection_account_calendar"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calendar_sync_account.id", ondelete="CASCADE"), index=True
    )
    google_calendar_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    # Default owners of events without a title tag / known attendee; empty = whole family.
    # Validated against the account owner's family on write (stale ids are ignored when syncing).
    owner_user_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list, server_default=text("'{}'::uuid[]")
    )
