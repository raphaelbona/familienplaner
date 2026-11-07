"""Family-scoped, bounded, expiring cache for discovered recipe candidates.

Candidates shown to a parent by ``POST /recipes/discover`` are kept here so they can be
adopted later by id. Entries are scoped to one family (another family can never resolve
them), expire after ``ttl_seconds`` and are bounded per family and globally (oldest
entries are evicted first). ``put_many`` *adds* to the family's entries instead of
replacing them, so candidates from an earlier response stay adoptable until they expire.
"""

import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable

from app.core.recipe_sources.types import RecipeCandidate


class DiscoverCache:
    def __init__(
        self,
        ttl_seconds: float = 3600,
        max_per_family: int = 50,
        max_total: int = 2000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_per_family = max_per_family
        self._max_total = max_total
        self._clock = clock
        self._lock = threading.Lock()
        # family_id -> OrderedDict[candidate_id -> (inserted_at, candidate)], oldest first
        self._data: dict[uuid.UUID, OrderedDict[str, tuple[float, RecipeCandidate]]] = {}

    def put_many(self, family_id: uuid.UUID, items: Iterable[tuple[str, RecipeCandidate]]) -> None:
        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            bucket = self._data.setdefault(family_id, OrderedDict())
            for candidate_id, candidate in items:
                bucket[candidate_id] = (now, candidate)
                bucket.move_to_end(candidate_id)
            while len(bucket) > self._max_per_family:
                bucket.popitem(last=False)
            while self._total() > self._max_total:
                self._evict_globally_oldest()

    def get(self, family_id: uuid.UUID, candidate_id: str) -> RecipeCandidate | None:
        with self._lock:
            entry = self._data.get(family_id, {}).get(candidate_id)
            if entry is None:
                return None
            inserted_at, candidate = entry
            if self._clock() - inserted_at > self._ttl:
                del self._data[family_id][candidate_id]
                return None
            return candidate

    def size(self, family_id: uuid.UUID) -> int:
        with self._lock:
            return len(self._data.get(family_id, {}))

    def total_size(self) -> int:
        with self._lock:
            return self._total()

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    # -- internals (lock held) -------------------------------------------------

    def _total(self) -> int:
        return sum(len(bucket) for bucket in self._data.values())

    def _purge_expired(self, now: float) -> None:
        for family_id in list(self._data):
            bucket = self._data[family_id]
            for candidate_id in [cid for cid, (ts, _) in bucket.items() if now - ts > self._ttl]:
                del bucket[candidate_id]
            if not bucket:
                del self._data[family_id]

    def _evict_globally_oldest(self) -> None:
        oldest_family = min(
            (fid for fid, bucket in self._data.items() if bucket),
            key=lambda fid: next(iter(self._data[fid].values()))[0],
        )
        self._data[oldest_family].popitem(last=False)
        if not self._data[oldest_family]:
            del self._data[oldest_family]
