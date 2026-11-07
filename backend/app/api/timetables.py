import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_parent
from app.core.storage import read_bounded_upload, save_upload
from app.core.task_recurrence import reconcile_family_task_instances_safely
from app.core.timetable_calendar import replace_timetable_calendar_events
from app.core.vision import interpret_timetable_image
from app.db.session import get_db
from app.models.timetable import TimetableEntry
from app.models.user import User, UserRole
from app.schemas.timetable import (
    TimetableBulkAssign,
    TimetableEntryCreate,
    TimetableEntryRead,
    TimetableEntryUpdate,
)

router = APIRouter(prefix="/timetables", tags=["timetables"])


def _validate_child_in_family(
    child_user_id: uuid.UUID, current_user: User, db: Session
) -> User:
    """Validate that child_user_id refers to a CHILD in the current user's family.

    Returns the child user if valid.
    Raises 404 if not found or not in the same family (generic message, no leakage).
    """
    child = db.get(User, child_user_id)
    if child is None or child.role != UserRole.CHILD or child.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nicht gefunden")
    return child


@router.post("/upload", response_model=list[TimetableEntryRead], status_code=status.HTTP_201_CREATED)
def upload_timetable(
    child_user_id: Annotated[uuid.UUID, Form()],
    file: Annotated[UploadFile, File()],
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
) -> list[TimetableEntry]:
    """Upload a timetable image and extract entries using Claude vision."""

    # Validate child is in the same family
    _validate_child_in_family(child_user_id, current_user, db)

    # Validate file is an image
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur Bilddateien werden unterstützt.")

    # Read file with bounded size checking
    max_size = 10 * 1024 * 1024  # 10 MB
    try:
        content = read_bounded_upload(file, request, max_size)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Save the upload (optional: only store for reference)
    try:
        save_upload(current_user.family_id, file.filename or "timetable.png", content)
    except (OSError, ValueError):
        # Not critical if storage fails; continue with interpretation
        pass

    # Interpret the image using Claude vision
    try:
        entries = interpret_timetable_image(content, file.content_type)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Delete existing timetable entries for this child (replace, don't append)
    db.execute(delete(TimetableEntry).where(TimetableEntry.child_user_id == child_user_id))
    db.commit()

    # Insert new timetable entries, skipping any with missing required fields
    created_entries: list[TimetableEntry] = []
    for entry_data in entries:
        # Skip if required fields are missing
        if not all(
            key in entry_data and entry_data[key] is not None
            for key in ["day_of_week", "start_time", "end_time", "subject"]
        ):
            continue

        entry = TimetableEntry(
            family_id=current_user.family_id,
            child_user_id=child_user_id,
            day_of_week=entry_data.get("day_of_week"),
            start_time=entry_data.get("start_time"),
            end_time=entry_data.get("end_time"),
            subject=entry_data.get("subject"),
            room=entry_data.get("room"),
        )
        db.add(entry)
        created_entries.append(entry)

    db.commit()
    for entry in created_entries:
        db.refresh(entry)

    # Reflect the new schedule in the family calendar (see app.core.timetable_calendar)
    replace_timetable_calendar_events(db, current_user.family_id, child_user_id)
    reconcile_family_task_instances_safely(db, current_user.family_id)

    return created_entries


def _get_entry_in_family(entry_id: uuid.UUID, current_user: User, db: Session) -> TimetableEntry:
    entry = db.get(TimetableEntry, entry_id)
    if entry is None or entry.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nicht gefunden")
    return entry


@router.post("", response_model=TimetableEntryRead, status_code=status.HTTP_201_CREATED)
def create_timetable_entry(
    payload: TimetableEntryCreate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> TimetableEntry:
    """Manually add a single timetable entry (used when image recognition fails or is unavailable)."""

    _validate_child_in_family(payload.child_user_id, current_user, db)

    entry = TimetableEntry(
        family_id=current_user.family_id,
        child_user_id=payload.child_user_id,
        day_of_week=payload.day_of_week,
        start_time=payload.start_time,
        end_time=payload.end_time,
        subject=payload.subject,
        room=payload.room,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    replace_timetable_calendar_events(db, current_user.family_id, payload.child_user_id)
    reconcile_family_task_instances_safely(db, current_user.family_id)

    return entry


@router.post("/bulk-assign", response_model=list[TimetableEntryRead])
def bulk_assign_timetable_entries(
    payload: TimetableBulkAssign,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> list[TimetableEntry]:
    """Assign one subject/room to multiple weekly slots at once (weekly-grid manual entry).

    Existing entries at a given day+time slot are overwritten; slots without an entry yet
    get a new one created.
    """

    _validate_child_in_family(payload.child_user_id, current_user, db)

    existing_by_slot = {
        (entry.day_of_week, entry.start_time, entry.end_time): entry
        for entry in db.scalars(
            select(TimetableEntry).where(TimetableEntry.child_user_id == payload.child_user_id)
        )
    }

    affected_entries: list[TimetableEntry] = []
    for slot in payload.slots:
        key = (slot.day_of_week, slot.start_time, slot.end_time)
        entry = existing_by_slot.get(key)
        if entry is not None:
            entry.subject = payload.subject
            entry.room = payload.room
        else:
            entry = TimetableEntry(
                family_id=current_user.family_id,
                child_user_id=payload.child_user_id,
                day_of_week=slot.day_of_week,
                start_time=slot.start_time,
                end_time=slot.end_time,
                subject=payload.subject,
                room=payload.room,
            )
            db.add(entry)
        affected_entries.append(entry)

    db.commit()
    for entry in affected_entries:
        db.refresh(entry)

    replace_timetable_calendar_events(db, current_user.family_id, payload.child_user_id)
    reconcile_family_task_instances_safely(db, current_user.family_id)

    return affected_entries


@router.patch("/{entry_id}", response_model=TimetableEntryRead)
def update_timetable_entry(
    entry_id: uuid.UUID,
    payload: TimetableEntryUpdate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> TimetableEntry:
    """Edit a single manually- or upload-created timetable entry."""

    entry = _get_entry_in_family(entry_id, current_user, db)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(entry, field, value)

    db.commit()
    db.refresh(entry)

    replace_timetable_calendar_events(db, current_user.family_id, entry.child_user_id)
    reconcile_family_task_instances_safely(db, current_user.family_id)

    return entry


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_timetable_entry(
    entry_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    entry = _get_entry_in_family(entry_id, current_user, db)
    child_user_id = entry.child_user_id

    db.delete(entry)
    db.commit()

    replace_timetable_calendar_events(db, current_user.family_id, child_user_id)
    reconcile_family_task_instances_safely(db, current_user.family_id)


@router.get("/{child_user_id}", response_model=list[TimetableEntryRead])
def get_timetable(
    child_user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[TimetableEntry]:
    """Get the timetable entries for a child.

    The child must be in the same family as the current user.
    Returns an empty list if the child is not found or not in the same family.
    """
    child = db.get(User, child_user_id)
    if child is None or child.role != UserRole.CHILD or child.family_id != current_user.family_id:
        # Return empty list for "not found" (consistent with other GET endpoints)
        return []

    stmt = (
        select(TimetableEntry)
        .where(TimetableEntry.child_user_id == child_user_id)
        .order_by(TimetableEntry.day_of_week, TimetableEntry.start_time)
    )
    return list(db.scalars(stmt))
