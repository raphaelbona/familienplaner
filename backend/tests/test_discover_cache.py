"""Unit tests for the family-scoped discover cache (TTL + bounds)."""

import uuid

from tests.food_helpers import cand


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _cache(**kw):
    from app.core.discover_cache import DiscoverCache

    clock = Clock()
    return DiscoverCache(clock=clock, **kw), clock


def test_get_returns_item_for_same_family_only():
    cache, _ = _cache()
    fam_a, fam_b = uuid.uuid4(), uuid.uuid4()
    cache.put_many(fam_a, [("c1", cand("A", ["x"]))])
    assert cache.get(fam_a, "c1").title == "A"
    assert cache.get(fam_b, "c1") is None
    assert cache.get(fam_a, "nope") is None


def test_entries_expire_after_ttl():
    cache, clock = _cache(ttl_seconds=3600)
    fam = uuid.uuid4()
    cache.put_many(fam, [("c1", cand("A", ["x"]))])
    clock.now += 3599
    assert cache.get(fam, "c1") is not None
    clock.now += 2
    assert cache.get(fam, "c1") is None


def test_second_put_keeps_earlier_items():
    cache, _ = _cache()
    fam = uuid.uuid4()
    cache.put_many(fam, [("c1", cand("A", ["x"]))])
    cache.put_many(fam, [("c2", cand("B", ["y"]))])
    assert cache.get(fam, "c1") is not None
    assert cache.get(fam, "c2") is not None


def test_per_family_bound_evicts_oldest():
    cache, clock = _cache(max_per_family=3)
    fam = uuid.uuid4()
    for i in range(5):
        clock.now += 1
        cache.put_many(fam, [(f"c{i}", cand(f"T{i}", ["x"]))])
    assert cache.get(fam, "c0") is None and cache.get(fam, "c1") is None
    assert all(cache.get(fam, f"c{i}") for i in (2, 3, 4))
    assert cache.size(fam) == 3


def test_global_bound_evicts_oldest_across_families():
    cache, clock = _cache(max_per_family=10, max_total=4)
    fams = [uuid.uuid4() for _ in range(3)]
    for i in range(6):
        clock.now += 1
        cache.put_many(fams[i % 3], [(f"c{i}", cand(f"T{i}", ["x"]))])
    assert cache.total_size() == 4
    assert cache.get(fams[0], "c0") is None  # oldest evicted
    assert cache.get(fams[2], "c5") is not None  # newest kept


def test_expired_entries_are_purged_on_put():
    cache, clock = _cache(ttl_seconds=10)
    fam = uuid.uuid4()
    cache.put_many(fam, [("c1", cand("A", ["x"]))])
    clock.now += 100
    cache.put_many(fam, [("c2", cand("B", ["y"]))])
    assert cache.size(fam) == 1


def test_clear():
    cache, _ = _cache()
    fam = uuid.uuid4()
    cache.put_many(fam, [("c1", cand("A", ["x"]))])
    cache.clear()
    assert cache.total_size() == 0
