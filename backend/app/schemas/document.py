"""Schemas for document upload and event extraction."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, field_serializer


class ExtractedDocumentEventRead(BaseModel):
    """Schema for reading an extracted document event."""

    id: uuid.UUID
    title: str
    event_date: date
    event_time: str | None
    person_hint: str | None
    resolved_child_user_id: uuid.UUID | None
    status: str
    calendar_event_id: uuid.UUID | None

    model_config = {"from_attributes": True}


class ParentDocumentRead(BaseModel):
    """Schema for reading a parent document with its extracted events."""

    id: uuid.UUID
    original_filename: str
    content_type: str
    status: str
    created_at: datetime
    events: list[ExtractedDocumentEventRead]

    model_config = {"from_attributes": True}

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        """Serialize datetime to ISO format string."""
        return value.isoformat() if value else None


class DocumentEventResolveRequest(BaseModel):
    """Request payload for resolving a pending document event."""

    child_user_id: uuid.UUID | None
