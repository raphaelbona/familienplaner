"""API endpoints for parent document management and event extraction."""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_parent
from app.core.storage import read_bounded_upload, read_upload, save_upload
from app.core.vision import extract_letter_events
from app.db.session import get_db
from app.models.calendar import CalendarEvent
from app.models.document import ExtractedDocumentEvent, ParentDocument
from app.models.user import User, UserRole
from app.schemas.document import DocumentEventResolveRequest, ExtractedDocumentEventRead, ParentDocumentRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


def _create_calendar_event_for_extracted_event(
    db: Session, family_id: uuid.UUID, event_data: dict
) -> CalendarEvent | None:
    """Create a CalendarEvent from extracted document event data.

    Args:
        db: Database session.
        family_id: The family ID for the event.
        event_data: Dict with title, event_date, event_time keys.
                   event_date can be a Python date object (from DB) or JSON string "YYYY-MM-DD".
                   event_time can be a string "HH:MM" or None.

    Returns:
        The created CalendarEvent, or None if parsing fails (date/time validation error).
    """
    title = event_data.get("title", "Termin")
    event_date = event_data.get("event_date")
    event_time = event_data.get("event_time")

    # Parse event_date: could be a Python date object or a JSON string
    try:
        if isinstance(event_date, date):
            parsed_date = event_date
        elif isinstance(event_date, str):
            parsed_date = date.fromisoformat(event_date)
        else:
            logger.warning("Invalid event_date type: %s, expected str or date", type(event_date))
            return None
    except (ValueError, TypeError) as e:
        logger.warning("Failed to parse event_date '%s': %s", event_date, e)
        return None

    # Parse event_time if provided: must be "HH:MM" format
    parsed_time = None
    if event_time is not None:
        if isinstance(event_time, str):
            try:
                parsed_time = datetime.strptime(event_time, "%H:%M").time()
            except ValueError as e:
                logger.warning("Failed to parse event_time '%s': %s", event_time, e)
                return None
        else:
            logger.warning("Invalid event_time type: %s, expected str or None", type(event_time))
            return None

    # Create datetime for calendar event
    if parsed_time is None:
        # All-day event
        start = datetime.combine(parsed_date, datetime.min.time()).replace(tzinfo=UTC)
        end = start + timedelta(days=1)
        all_day = True
    else:
        # Timed event: end is start + 1 hour
        start = datetime.combine(parsed_date, parsed_time).replace(tzinfo=UTC)
        end = start + timedelta(hours=1)
        all_day = False

    event = CalendarEvent(
        family_id=family_id, title=title, start=start, end=end, all_day=all_day, source="parent_letter"
    )
    db.add(event)
    db.flush()  # Get the ID without committing
    return event


@router.post("/upload", response_model=ParentDocumentRead, status_code=status.HTTP_201_CREATED)
def upload_document(
    request: Request,
    file: Annotated[UploadFile, File()],
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> ParentDocument:
    """Upload a parent document (letter, notice) and extract events using Claude vision.

    Args:
        request: HTTP request (for Content-Length checking).
        file: Uploaded image file.
        current_user: Current parent user.
        db: Database session.

    Returns:
        The created ParentDocument with extracted events.
    """
    # Validate file is an allowed image type (explicit allowlist)
    allowed_types = {"image/png", "image/jpeg", "image/webp", "image/gif"}
    if not file.content_type or file.content_type not in allowed_types:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur Bilddateien werden unterstützt.")

    # Read file with bounded size checking (10 MB)
    max_size = 10 * 1024 * 1024
    try:
        content = read_bounded_upload(file, request, max_size)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Extract events using vision
    try:
        extracted_events = extract_letter_events(content, file.content_type)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Save the file to storage
    stored_path = save_upload(current_user.family_id, file.filename or "document.png", content)

    # Create the document record
    document = ParentDocument(
        family_id=current_user.family_id,
        uploaded_by=current_user.id,
        original_filename=file.filename or "document.png",
        stored_path=stored_path,
        content_type=file.content_type,
        status="processed",  # Will be updated if any events are pending
    )
    db.add(document)
    db.flush()  # Get the document ID

    # Process extracted events
    has_pending = False
    for event_data in extracted_events:
        person_hint = event_data.get("person_hint")

        # Parse and validate event_date: convert JSON string to Python date
        # If parsing fails, use document's creation date as fallback and mark as pending
        event_date_raw = event_data.get("event_date")
        parsed_date = None
        date_parse_error = False

        try:
            if isinstance(event_date_raw, date):
                parsed_date = event_date_raw
            elif isinstance(event_date_raw, str):
                parsed_date = date.fromisoformat(event_date_raw)
            else:
                logger.warning("Invalid event_date type: %s", type(event_date_raw))
                date_parse_error = True
        except (ValueError, TypeError) as e:
            logger.warning("Failed to parse event_date '%s': %s", event_date_raw, e)
            date_parse_error = True

        # If date parsing failed, use document's creation date as fallback
        # so the event is persisted for parent review
        if date_parse_error:
            parsed_date = document.created_at.date() if document.created_at else datetime.now(UTC).date()
            has_pending = True

        # Try to match person_hint against family's children (only if date was valid)
        resolved_child_user_id = None
        status_str = "pending_clarification"
        calendar_event_id = None

        if not date_parse_error and person_hint:
            # Find children in the family whose display_name contains the hint (case-insensitive)
            hint_lower = person_hint.lower()
            matching_children = db.scalars(
                select(User).where(
                    User.family_id == current_user.family_id,
                    User.role == UserRole.CHILD,
                )
            ).all()

            matches = [
                child for child in matching_children if hint_lower in child.display_name.lower()
            ]

            # Only auto-resolve if exactly one child matches
            if len(matches) == 1:
                # Try to create calendar event first
                cal_event = _create_calendar_event_for_extracted_event(db, current_user.family_id, event_data)
                if cal_event is not None:
                    # Calendar event created successfully: confirm the event
                    resolved_child_user_id = matches[0].id
                    status_str = "confirmed"
                    calendar_event_id = cal_event.id
                else:
                    # Date/time parsing failed: treat as pending clarification, don't resolve
                    status_str = "pending_clarification"
                    has_pending = True
            elif len(matches) > 1:
                # Ambiguous: multiple matches
                has_pending = True
            else:
                # No matches
                has_pending = True
        elif not date_parse_error and not person_hint:
            # No person_hint; needs clarification
            has_pending = True

        # Create the extracted event record
        extracted_event = ExtractedDocumentEvent(
            document_id=document.id,
            title=event_data.get("title", "Termin"),
            event_date=parsed_date,
            event_time=event_data.get("event_time"),
            person_hint=person_hint,
            resolved_child_user_id=resolved_child_user_id,
            status=status_str,
            calendar_event_id=calendar_event_id,
        )
        db.add(extracted_event)

    # Update document status if any events are pending
    if has_pending:
        document.status = "needs_clarification"

    db.commit()
    db.refresh(document)
    return document


@router.get("", response_model=list[ParentDocumentRead])
def list_documents(
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ParentDocument]:
    """List all documents for the current family.

    Returns documents ordered by creation date (newest first).
    """
    documents = db.scalars(
        select(ParentDocument)
        .where(ParentDocument.family_id == current_user.family_id)
        .order_by(ParentDocument.created_at.desc())
    ).all()
    return documents


@router.get("/{document_id}/file")
def get_document_file(
    document_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Download the original document file.

    Verifies the document belongs to the current family before serving.
    """
    document = db.get(ParentDocument, document_id)
    if document is None or document.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nicht gefunden")

    # Read the file content
    try:
        content = read_upload(document.stored_path)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Datei nicht gefunden") from None

    # Sanitize original filename for Content-Disposition header
    # Strip anything that isn't alphanumeric, dot, dash, underscore, or space
    safe_filename = "".join(
        c if (c.isalnum() or c in ".-_ ") else "" for c in document.original_filename
    )
    if not safe_filename:
        safe_filename = "document"

    # Use RFC 5987 percent-encoded form to be safe with special characters
    encoded_filename = quote(safe_filename, safe="")

    return Response(
        content=content,
        media_type=document.content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


@router.post("/{document_id}/events/{event_id}/resolve", response_model=ExtractedDocumentEventRead)
def resolve_document_event(
    document_id: uuid.UUID,
    event_id: uuid.UUID,
    payload: DocumentEventResolveRequest,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> ExtractedDocumentEvent:
    """Resolve (clarify or dismiss) a pending document event.

    Args:
        document_id: The document ID.
        event_id: The event ID.
        payload: Resolution payload with optional child_user_id.
        current_user: Current parent user.
        db: Database session.

    Returns:
        The updated ExtractedDocumentEvent.
    """
    # Verify document belongs to current family
    document = db.get(ParentDocument, document_id)
    if document is None or document.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nicht gefunden")

    # Verify event belongs to this document
    event = db.get(ExtractedDocumentEvent, event_id)
    if event is None or event.document_id != document_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nicht gefunden")

    # Handle the resolution
    if payload.child_user_id is not None:
        # Verify the child belongs to the current family and is a CHILD role
        child = db.get(User, payload.child_user_id)
        if child is None or child.role != UserRole.CHILD or child.family_id != current_user.family_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Nicht gefunden")

        # Create calendar event
        # Note: event.event_date and event.event_time are already Python types from DB, so parsing should not fail
        cal_event = _create_calendar_event_for_extracted_event(
            db,
            current_user.family_id,
            {
                "title": event.title,
                "event_date": event.event_date,
                "event_time": event.event_time,
            },
        )

        # Update the event
        event.resolved_child_user_id = payload.child_user_id
        event.status = "confirmed"
        if cal_event is not None:
            event.calendar_event_id = cal_event.id
        else:
            # This should not happen in practice (DB values should always parse),
            # but if it does, raise an error rather than silently losing the event
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Fehler beim Erstellen des Kalendereintrags")
    else:
        # Dismiss the event
        event.status = "dismissed"

    db.add(event)

    # Check if document still has any pending events
    remaining_pending = db.scalar(
        select(ExtractedDocumentEvent).where(
            ExtractedDocumentEvent.document_id == document_id,
            ExtractedDocumentEvent.status == "pending_clarification",
        )
    )

    if remaining_pending is None:
        # No more pending events; mark document as processed
        document.status = "processed"
        db.add(document)

    db.commit()
    db.refresh(event)
    return event
