"""School holidays and free days: which dates a break covers for which child, and how an all-day Google event
is recognized as a break.

The timetable is a weekly pattern; a break switches it off for its date range. The calendar side effects
(timetable-derived events) live in app.core.timetable_calendar.
"""

import re
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.school_break import SchoolBreak

KIND_FERIEN = "ferien"
KIND_SCHULFREI = "schulfrei"
KINDS = (KIND_FERIEN, KIND_SCHULFREI)
SOURCE_MANUAL = "manual"
SOURCE_GOOGLE = "google"
MAX_SPAN_DAYS = 400  # inclusive number of days one break may cover
MSG_FROM_GOOGLE = "Dieser Eintrag kommt aus Google und wird dort geändert."


def applies_to_child(brk: SchoolBreak, child_user_id: uuid.UUID) -> bool:
    """An empty child list means every child."""
    return not brk.child_user_ids or child_user_id in brk.child_user_ids


def covered_dates(
    db: Session, family_id: uuid.UUID, child_user_id: uuid.UUID, first: date, last: date
) -> set[date]:
    """The dates in first..last (inclusive) on which a break applies to this child."""
    rows = db.scalars(
        select(SchoolBreak).where(
            SchoolBreak.family_id == family_id,
            SchoolBreak.end_date >= first,
            SchoolBreak.start_date <= last,
        )
    )
    covered: set[date] = set()
    for brk in rows:
        if not applies_to_child(brk, child_user_id):
            continue
        day = max(brk.start_date, first)
        stop = min(brk.end_date, last)
        while day <= stop:
            covered.add(day)
            day += timedelta(days=1)
    return covered


def affected_children(*scopes: Sequence[uuid.UUID]) -> set[uuid.UUID] | None:
    """Children touched by changing breaks with the given child lists; None = every child (an empty list)."""
    if not scopes or any(not scope for scope in scopes):
        return None
    return {child for scope in scopes for child in scope}


# --------------------------------------------------------------------------------------------------------
# Recognizing a break in a Google event title


def _fold(text: str) -> str:
    """Lower case without accents/umlauts ("Schüler" == "schuler"), single spaces."""
    text = text.casefold().replace("ß", "ss")
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


_SCHULFREI_START = re.compile(r"^[\W_]*(schulfrei|schule frei|unterrichtsfrei)")


def classify_break_title(title: str) -> str | None:
    """"ferien" / "schulfrei" when an all-day event with this title (member tag already removed) is a break.

    "ferien" anywhere in the title ("Herbstferien", "Ferien: Herbst") means holidays; a title that starts with
    "Schulfrei", "Schule frei" or "Unterrichtsfrei" is a single free day. Known limitation: an all-day
    "Ferienwohnung buchen" matches as well.
    """
    folded = _fold(title)
    if _SCHULFREI_START.match(folded):
        return KIND_SCHULFREI
    if "ferien" in folded:
        return KIND_FERIEN
    return None


def exclusive_end_to_last_day(first_day: date, exclusive_end: date) -> date:
    """Google's all-day ``end.date`` is exclusive; a missing/odd end means a one-day event."""
    return max(first_day, exclusive_end - timedelta(days=1))


def dedupe(ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    return list(dict.fromkeys(ids))
