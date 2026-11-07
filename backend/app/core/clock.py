"""The family's local date and time (settings.app_timezone, Europe/Zurich by default).

One place so that "today" means the same everywhere in the task logic and tests can pin it.
"""

from datetime import UTC, date, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import get_settings

DEFAULT_TIMEZONE = "Europe/Zurich"


def local_timezone() -> tzinfo:
    for name in (get_settings().app_timezone, DEFAULT_TIMEZONE):
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return UTC


def local_now() -> datetime:
    """Timezone-aware current time in the family's timezone."""
    return datetime.now(local_timezone())


def local_today() -> date:
    return local_now().date()
