"""Rate-limited WARNING logging for recipe-source failures (no log spam, no secrets)."""

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger("app.core.recipe_sources.health")

LOG_INTERVAL_SECONDS = 60.0
_lock = threading.Lock()
_last_logged: dict[str, float] = {}
_clock: Callable[[], float] = time.monotonic


def log_source_failure(source: str, reason: object) -> None:
    """Log at WARNING at most once per source per minute; only the exception class is logged."""
    detail = type(reason).__name__ if isinstance(reason, BaseException) else str(reason)[:80]
    now = _clock()
    with _lock:
        last = _last_logged.get(source)
        if last is not None and now - last < LOG_INTERVAL_SECONDS:
            return
        _last_logged[source] = now
    logger.warning("Recipe source %s failed: %s", source, detail)


def unreachable_warning(display_name: str) -> str:
    return f"{display_name} war nicht erreichbar."


def overloaded_warning(display_name: str) -> str:
    return f"{display_name} ist gerade überlastet – bitte später erneut versuchen."
