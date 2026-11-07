"""Is a day a school day or a free day for a child? Decides which day phases apply (app.models.day_phase).

A school day: the child has at least one timetable entry on that weekday AND no school break (ferien or
schulfrei, see app.core.school_breaks) applies to the child on that date. Everything else is a free day.
"""

import uuid
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.school_breaks import covered_dates
from app.models.day_phase import DAY_TYPE_BOTH, DayPhase
from app.models.timetable import TimetableEntry

DayType = Literal["school", "free"]


def school_weekdays(db: Session, family_id: uuid.UUID, child_user_id: uuid.UUID) -> set[int]:
    """Weekdays (0=Mon..6=Sun) on which the child has at least one timetable entry."""
    return set(
        db.scalars(
            select(TimetableEntry.day_of_week)
            .where(TimetableEntry.family_id == family_id, TimetableEntry.child_user_id == child_user_id)
            .distinct()
        )
    )


def day_types_for_range(
    db: Session, child_user_id: uuid.UUID, family_id: uuid.UUID, first: date, last: date
) -> dict[date, DayType]:
    """The day type of every date in first..last (inclusive); timetable and breaks are loaded once."""
    weekdays = school_weekdays(db, family_id, child_user_id)
    breaks = covered_dates(db, family_id, child_user_id, first, last) if weekdays else set()
    result: dict[date, DayType] = {}
    day = first
    while day <= last:
        result[day] = "school" if day.weekday() in weekdays and day not in breaks else "free"
        day += timedelta(days=1)
    return result


def day_type_for(db: Session, child_user_id: uuid.UUID, family_id: uuid.UUID, on_date: date) -> DayType:
    return day_types_for_range(db, child_user_id, family_id, on_date, on_date)[on_date]


def phase_applies(phase: DayPhase, day_type: str) -> bool:
    """A phase exists on a day when it is for both kinds of day or for this kind."""
    return phase.day_type == DAY_TYPE_BOTH or phase.day_type == day_type
