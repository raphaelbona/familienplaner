"""Generates a rolling window of CalendarEvent occurrences for a recurring event.

Mirrors app.core.task_recurrence's pattern: the event a user creates becomes the
series root (it carries the recurrence_rule itself); this tops up the following
occurrences as their own CalendarEvent rows, linked back via series_id.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.calendar import CalendarEvent

DAILY_WINDOW_DAYS = 14
WEEKLY_WINDOW_DAYS = 56  # ~8 occurrences


def generate_occurrences_for_series(
    db: Session, root: CalendarEvent, today: datetime | None = None
) -> list[CalendarEvent]:
    """Top up the rolling window of occurrence rows for a recurring event's series."""
    if today is None:
        today = datetime.now(root.start.tzinfo or UTC)

    duration = root.end - root.start
    if root.recurrence_rule == "daily":
        window_end = today + timedelta(days=DAILY_WINDOW_DAYS)
        step = timedelta(days=1)
    else:
        window_end = today + timedelta(days=WEEKLY_WINDOW_DAYS)
        step = timedelta(days=7)

    existing_starts = set(
        db.scalars(select(CalendarEvent.start).where(CalendarEvent.series_id == root.id))
    )
    existing_starts.add(root.start)

    created: list[CalendarEvent] = []
    current = root.start
    while current <= window_end:
        if current >= today and current not in existing_starts:
            event = CalendarEvent(
                family_id=root.family_id,
                owner_user_id=root.owner_user_id,
                title=root.title,
                start=current,
                end=current + duration,
                all_day=root.all_day,
                color=root.color,
                source=root.source,
                series_id=root.id,
            )
            db.add(event)
            created.append(event)
            existing_starts.add(current)
        current += step

    db.commit()
    for event in created:
        db.refresh(event)
    return created
