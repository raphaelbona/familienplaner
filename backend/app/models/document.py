"""Models for parent document storage and event extraction."""

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPk


class ParentDocument(Base, UUIDPk, TimestampMixin):
    """A parent-uploaded document (letter, notice, etc.) for event extraction."""

    __tablename__ = "parent_document"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE")
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="processed")

    # "needs_clarification" | "processed"
    events: Mapped[list["ExtractedDocumentEvent"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class ExtractedDocumentEvent(Base, UUIDPk):
    """An event extracted from a parent document."""

    __tablename__ = "extracted_document_event"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent_document.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(200))
    event_date: Mapped[date] = mapped_column(Date)
    event_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    person_hint: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolved_child_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="pending_clarification")

    # "pending_clarification" | "confirmed" | "dismissed"
    calendar_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calendar_event.id", ondelete="SET NULL"), nullable=True
    )

    document: Mapped["ParentDocument"] = relationship(back_populates="events")
