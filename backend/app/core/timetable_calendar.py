"""Generates CalendarEvent rows from a child's TimetableEntry rows.

Lets the weekly class schedule show up automatically in the family calendar
instead of only living in the separate timetable view. Dates covered by a school break
(app.models.school_break) that applies to the child get no lessons.
"""

import uuid
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.school_breaks import covered_dates
from app.models.calendar import CalendarEvent
from app.models.timetable import TimetableEntry
from app.models.user import User, UserRole

WINDOW_DAYS = 42  # ~6 weeks rolling window of upcoming lessons
SOURCE = "timetable"


def _occurrence_dates(start: date, window_days: int, weekday: int) -> list[date]:
    return [
        start + timedelta(days=offset)
        for offset in range(window_days)
        if (start + timedelta(days=offset)).weekday() == weekday
    ]


def _build_event(
    family_id: uuid.UUID, child_user_id: uuid.UUID, entry: TimetableEntry, occurrence: date
) -> CalendarEvent:
    start_time = datetime.strptime(entry.start_time, "%H:%M").time()
    end_time = datetime.strptime(entry.end_time, "%H:%M").time()
    title = f"{entry.subject} ({entry.room})" if entry.room else entry.subject
    return CalendarEvent(
        family_id=family_id,
        owner_user_id=child_user_id,
        title=title,
        start=datetime.combine(occurrence, start_time),
        end=datetime.combine(occurrence, end_time),
        all_day=False,
        source=SOURCE,
        external_id=f"{entry.id}:{occurrence.isoformat()}",
    )


def _window_covered(db: Session, family_id: uuid.UUID, child_user_id: uuid.UUID, start: date) -> set[date]:
    return covered_dates(db, family_id, child_user_id, start, start + timedelta(days=WINDOW_DAYS - 1))


def replace_timetable_calendar_events(
    db: Session, family_id: uuid.UUID, child_user_id: uuid.UUID, today: date | None = None
) -> None:
    """Fully regenerate the rolling window of timetable-derived calendar events for a child.

    Called right after a (re-)upload, since the timetable content itself may have changed.
    """
    start = today or date.today()
    covered = _window_covered(db, family_id, child_user_id, start)

    db.execute(
        delete(CalendarEvent).where(
            CalendarEvent.owner_user_id == child_user_id, CalendarEvent.source == SOURCE
        )
    )

    entries = list(db.scalars(select(TimetableEntry).where(TimetableEntry.child_user_id == child_user_id)))
    for entry in entries:
        for occurrence in _occurrence_dates(start, WINDOW_DAYS, entry.day_of_week):
            if occurrence in covered:
                continue
            db.add(_build_event(family_id, child_user_id, entry, occurrence))

    db.commit()


def extend_timetable_calendar_events(
    db: Session, family_id: uuid.UUID, child_user_id: uuid.UUID, today: date | None = None
) -> None:
    """Top up the rolling window without touching existing events (periodic worker job)."""
    start = today or date.today()

    entries = list(db.scalars(select(TimetableEntry).where(TimetableEntry.child_user_id == child_user_id)))
    if not entries:
        return

    covered = _window_covered(db, family_id, child_user_id, start)
    existing_external_ids = set(
        db.scalars(
            select(CalendarEvent.external_id).where(
                CalendarEvent.owner_user_id == child_user_id,
                CalendarEvent.source == SOURCE,
                CalendarEvent.start >= datetime.combine(start, time.min),
            )
        )
    )

    for entry in entries:
        for occurrence in _occurrence_dates(start, WINDOW_DAYS, entry.day_of_week):
            external_id = f"{entry.id}:{occurrence.isoformat()}"
            if occurrence in covered or external_id in existing_external_ids:
                continue
            db.add(_build_event(family_id, child_user_id, entry, occurrence))

    db.commit()


def reconcile_timetable_calendar_events(
    db: Session, family_id: uuid.UUID, child_user_id: uuid.UUID, today: date | None = None
) -> None:
    """Bring the child's timetable events in the rolling window in line with the timetable and the breaks.

    Diff based and idempotent: events on dates covered by a break are deleted, lessons on dates that are not
    (or no longer) covered are created. Events of past days are never touched. Does not commit.
    """
    start = today or date.today()
    last = start + timedelta(days=WINDOW_DAYS - 1)
    covered = _window_covered(db, family_id, child_user_id, start)

    entries = list(db.scalars(select(TimetableEntry).where(TimetableEntry.child_user_id == child_user_id)))
    desired: dict[str, tuple[TimetableEntry, date]] = {}
    for entry in entries:
        for occurrence in _occurrence_dates(start, WINDOW_DAYS, entry.day_of_week):
            if occurrence not in covered:
                desired[f"{entry.id}:{occurrence.isoformat()}"] = (entry, occurrence)

    existing = {
        row.external_id: row
        for row in db.scalars(
            select(CalendarEvent).where(
                CalendarEvent.family_id == family_id,
                CalendarEvent.owner_user_id == child_user_id,
                CalendarEvent.source == SOURCE,
            )
        )
        if row.external_id
    }
    first_iso, last_iso = start.isoformat(), last.isoformat()
    for external_id, row in existing.items():
        day = external_id.rsplit(":", 1)[-1]
        if first_iso <= day <= last_iso and external_id not in desired:
            db.delete(row)
    for external_id, (entry, occurrence) in desired.items():
        if external_id not in existing:
            db.add(_build_event(family_id, child_user_id, entry, occurrence))


def reconcile_family_timetables(
    db: Session,
    family_id: uuid.UUID,
    child_user_ids: Iterable[uuid.UUID] | None = None,
    today: date | None = None,
) -> None:
    """Reconcile the timetable events of the given children of a family (None = all children with a timetable).

    The one entry point after a school break was created, changed, removed or imported. Ids that are not
    children of this family are ignored. Commits.
    """
    stmt = (
        select(TimetableEntry.child_user_id)
        .join(User, User.id == TimetableEntry.child_user_id)
        .where(TimetableEntry.family_id == family_id, User.family_id == family_id, User.role == UserRole.CHILD)
        .distinct()
    )
    if child_user_ids is not None:
        stmt = stmt.where(TimetableEntry.child_user_id.in_(list(child_user_ids)))
    for child_user_id in list(db.scalars(stmt)):
        reconcile_timetable_calendar_events(db, family_id, child_user_id, today)
    db.commit()
