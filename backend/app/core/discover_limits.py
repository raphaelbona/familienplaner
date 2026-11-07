"""Abuse protection for ``POST /recipes/discover`` (in-process, per worker).

* ``DiscoverRateLimiter``: per family, at least ``min_interval`` seconds between accepted calls and
  at most ``daily_cap`` accepted calls per UTC day. Rejected attempts neither extend the window nor
  count against the cap. Memory is bounded (``max_families``, stale entries are evicted first).
* ``DiscoverSlots``: global cap on concurrently running discovery runs (non-blocking).

Both take injectable clocks / sizes so tests do not have to sleep. State is per process: with
several workers the effective limits multiply by the worker count (acceptable for this app).
"""

import math
import threading
import time
from collections.abc import Callable, Hashable

DAY_SECONDS = 86400


class DiscoverRateLimited(Exception):
    def __init__(self, retry_after: int, reason: str) -> None:
        super().__init__(reason)
        self.retry_after = max(1, retry_after)
        self.reason = reason


class DiscoverRateLimiter:
    def __init__(
        self,
        min_interval: float = 10.0,
        daily_cap: int = 100,
        clock: Callable[[], float] = time.time,
        max_families: int = 10_000,
    ) -> None:
        self.min_interval = min_interval
        self.daily_cap = daily_cap
        self._clock = clock
        self._max_families = max_families
        self._lock = threading.Lock()
        self._state: dict[Hashable, tuple[float, int, int]] = {}  # family -> (last_call, day, count)

    @property
    def tracked_families(self) -> int:
        return len(self._state)

    def check(self, family_id: Hashable) -> None:
        """Register one call for ``family_id`` or raise ``DiscoverRateLimited``."""
        now = self._clock()
        day = int(now // DAY_SECONDS)
        with self._lock:
            last, state_day, count = self._state.get(family_id, (-math.inf, day, 0))
            if state_day != day:
                count = 0
            wait = last + self.min_interval - now
            if wait > 0:
                raise DiscoverRateLimited(math.ceil(wait), "too many requests")
            if count >= self.daily_cap:
                raise DiscoverRateLimited(math.ceil((day + 1) * DAY_SECONDS - now), "daily limit reached")
            self._state.pop(family_id, None)  # re-insert = most recently used
            self._state[family_id] = (now, day, count + 1)
            self._evict(day)

    def _evict(self, today: int) -> None:
        if len(self._state) <= self._max_families:
            return
        for key in [k for k, (_, d, _c) in self._state.items() if d != today]:
            del self._state[key]
        while len(self._state) > self._max_families:  # dicts keep insertion order: oldest first
            del self._state[next(iter(self._state))]


class DiscoverSlots:
    """Bounded number of concurrent discovery runs; never blocks."""

    def __init__(self, max_concurrent: int = 4) -> None:
        self._sem = threading.BoundedSemaphore(max_concurrent)

    def try_acquire(self) -> bool:
        return self._sem.acquire(blocking=False)

    def release(self) -> None:
        self._sem.release()
