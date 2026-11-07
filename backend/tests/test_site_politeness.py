"""Site-search politeness: request pacing, 429/503 circuit breaker, result cache. Fake clock, no live network."""

import json
import socket

import httpx
import pytest

from app.core.config import Settings
from app.core.recipe_sources import site_search
from app.core.recipe_sources.chefkoch import ChefkochSearchSource
from app.core.recipe_sources.site_search import SearchCache, SiteGate

OVERLOADED = "Chefkoch.de ist gerade überlastet – bitte später erneut versuchen."
RECIPE_LD = {"@type": "Recipe", "name": "Möhrengemüse", "recipeIngredient": ["500 g Karotten", "Butter"]}
IDS = [1274521232808429 + i for i in range(6)]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    )
    monkeypatch.setattr(site_search, "get_settings", lambda: Settings(recipe_search_enabled=True))


def _ok_routes(request):
    if request.url.path.startswith("/rs/s0/"):
        links = "".join(f'<a href="/rezepte/{i}/Moehren-{n}.html">r</a>' for n, i in enumerate(IDS))
        return httpx.Response(200, text=f"<html>{links}</html>")
    ld = json.dumps({**RECIPE_LD, "name": f"Rezept {request.url.path}"})
    return httpx.Response(200, text=f'<script type="application/ld+json">{ld}</script>')


def _make(routes, clock=None, gate=None, cache=None, budget=60.0):
    clock = clock or Clock()
    seen: list[tuple[float, httpx.Request]] = []

    def handler(request):
        seen.append((clock(), request))
        return routes(request)

    gate = gate or SiteGate(min_interval=0.0, clock=clock, sleeper=clock.advance)
    cache = cache or SearchCache(clock=clock)
    src = ChefkochSearchSource(
        transport=httpx.MockTransport(handler), time_budget=budget, clock=clock, gate=gate, cache=cache
    )
    return src, seen, clock, gate, cache


# ---------------------------------------------------------------- pacing


def test_requests_are_spaced_by_the_minimum_interval():
    clock = Clock()
    gate = SiteGate(min_interval=1.0, clock=clock, sleeper=clock.advance)
    src, seen, _, _, _ = _make(_ok_routes, clock, gate)
    assert len(src.search(["Karotten"], limit=10)) == 3
    times = [t for t, _ in seen]
    assert len(times) == 4
    assert all(b - a >= 1.0 for a, b in zip(times, times[1:], strict=False))


def test_pacing_state_is_shared_between_source_instances():
    clock = Clock()
    gate = SiteGate(min_interval=1.0, clock=clock, sleeper=clock.advance)
    first, seen, _, _, _ = _make(_ok_routes, clock, gate, SearchCache(clock=clock))
    second, seen2, *_ = _make(_ok_routes, clock, gate, SearchCache(clock=clock))
    first.search(["Karotten"])
    second.search(["Reis"])
    times = [t for t, _ in seen] + [t for t, _ in seen2]
    assert all(b - a >= 1.0 for a, b in zip(times, times[1:], strict=False))


def test_waiting_beyond_the_time_budget_stops_instead_of_sleeping():
    clock = Clock()
    slept: list[float] = []

    def sleeper(seconds):
        slept.append(seconds)
        clock.advance(seconds)

    gate = SiteGate(min_interval=5.0, clock=clock, sleeper=sleeper)
    src, seen, *_ = _make(_ok_routes, clock, gate, budget=8.0)
    src.search(["Karotten"], limit=10)
    assert len(seen) == 2  # t=0 search page, t=5 first recipe; the next slot (t=10) is beyond the 8 s budget
    assert slept == [5.0]
    assert clock.now - 1000.0 <= 8.0


def test_gate_reserve_returns_none_without_reserving_when_too_long():
    clock = Clock()
    gate = SiteGate(min_interval=10.0, clock=clock)
    assert gate.reserve(1.0) == 0.0
    assert gate.reserve(1.0) is None
    clock.advance(9.5)
    assert gate.reserve(1.0) == pytest.approx(0.5)  # the refused call did not push the slot back


# ---------------------------------------------------------------- 429 / 503 and circuit breaker


@pytest.mark.parametrize("status", [429, 503])
def test_overload_is_a_failure_with_german_warning_and_stops_requests(status):
    src, seen, *_ = _make(lambda r: httpx.Response(status))
    result = src.search_with_status(["Karotten"])
    assert result.failed is True and result.candidates == []
    assert result.warning == OVERLOADED
    assert len(seen) == 1


def test_overload_on_a_recipe_page_stops_further_pages_and_keeps_earlier_ones():
    def routes(request):
        if request.url.path.startswith("/rs/s0/") or str(IDS[0]) in request.url.path:
            return _ok_routes(request)
        return httpx.Response(429)

    src, seen, *_ = _make(routes)
    result = src.search_with_status(["Karotten"], limit=10)
    assert len(result.candidates) == 1
    assert result.failed is True and result.warning == OVERLOADED
    assert len(seen) == 3  # search, 1st page ok, 2nd page 429 -> no 3rd page


def test_breaker_skips_the_source_without_any_request_but_keeps_warning():
    src, seen, clock, gate, _ = _make(lambda r: httpx.Response(429))
    src.search_with_status(["Karotten"])
    n = len(seen)
    again = src.search_with_status(["Reis"])
    assert len(seen) == n
    assert again.failed is True and again.warning == OVERLOADED
    other, other_seen, *_ = _make(_ok_routes, clock, gate)  # a new instance shares the breaker via the gate
    assert other.search_with_status(["Tomaten"]).warning == OVERLOADED
    assert other_seen == []


def test_breaker_closes_after_the_default_cooldown_of_five_minutes():
    responses = [httpx.Response(429)]
    src, seen, clock, *_ = _make(lambda r: responses.pop(0) if responses else _ok_routes(r))
    src.search_with_status(["Karotten"])
    clock.advance(299)
    assert src.search_with_status(["Karotten"]).failed is True
    assert len(seen) == 1
    clock.advance(2)
    result = src.search_with_status(["Karotten"])
    assert result.failed is False and len(result.candidates) == 3


def test_retry_after_seconds_extends_the_cooldown_up_to_a_cap():
    for retry_after, blocked_until in [("120", 120.0), ("99999", 300.0), ("abc", 60.0), ("-5", 60.0)]:
        clock = Clock()
        gate = SiteGate(min_interval=0.0, cooldown=60.0, clock=clock, sleeper=clock.advance)
        src, *_ = _make(lambda r, ra=retry_after: httpx.Response(429, headers={"retry-after": ra}), clock, gate)
        src.search_with_status(["Karotten"])
        clock.advance(blocked_until - 1)
        assert gate.breaker_open(), retry_after
        clock.advance(2)
        assert not gate.breaker_open(), retry_after


# ---------------------------------------------------------------- cache


def test_successful_search_is_cached_per_normalised_term():
    src, seen, *_ = _make(_ok_routes)
    first = src.search(["Karotten"], limit=10)
    n = len(seen)
    assert n == 4
    second = src.search(["  karotten "], limit=10)
    assert len(seen) == n
    assert [c.source_url for c in second] == [c.source_url for c in first]
    src.search(["Reis"])
    assert len(seen) > n  # a different term is a different key


def test_cache_is_shared_between_source_instances_and_returns_copies():
    clock = Clock()
    cache = SearchCache(clock=clock)
    a, seen, _, gate, _ = _make(_ok_routes, clock, cache=cache)
    b, seen_b, *_ = _make(_ok_routes, clock, gate, cache)
    a.search(["Karotten"])[0].title = "verändert"
    assert seen_b == []
    assert b.search(["Karotten"])[0].title != "verändert"
    assert seen_b == []


def test_cache_expires_after_six_hours():
    src, seen, clock, *_ = _make(_ok_routes)
    src.search(["Karotten"])
    n = len(seen)
    clock.advance(6 * 3600 - 1)
    src.search(["Karotten"])
    assert len(seen) == n
    clock.advance(2)
    src.search(["Karotten"])
    assert len(seen) == 2 * n


@pytest.mark.parametrize("status", [429, 503, 500])
def test_failures_are_not_cached_as_empty_results(status):
    state = {"fail": True}
    src, seen, clock, gate, _ = _make(lambda r: httpx.Response(status) if state["fail"] else _ok_routes(r))
    assert src.search_with_status(["Karotten"]).failed is True
    state["fail"] = False
    clock.advance(301)  # past the breaker cool-down
    result = src.search_with_status(["Karotten"])
    assert result.failed is False and len(result.candidates) == 3


def test_partial_failure_is_not_cached():
    calls = {"n": 0}

    def routes(request):
        if request.url.path.startswith("/rs/s0/") or calls["n"] > 0:
            return _ok_routes(request)
        calls["n"] += 1
        return httpx.Response(404)  # first recipe page missing

    src, seen, *_ = _make(routes)
    src.search(["Karotten"])
    n = len(seen)
    src.search(["Karotten"])
    assert len(seen) > n


def test_genuinely_empty_search_is_cached():
    src, seen, *_ = _make(lambda r: httpx.Response(200, text="<html></html>"))
    assert src.search_with_status(["Xyzzy"]).failed is False
    src.search_with_status(["Xyzzy"])
    assert len(seen) == 1


def test_cache_cached_results_still_served_while_breaker_is_open():
    src, seen, clock, gate, cache = _make(_ok_routes)
    src.search(["Karotten"])
    gate.open_breaker()
    n = len(seen)
    result = src.search_with_status(["Karotten"])
    assert len(seen) == n and result.failed is False and len(result.candidates) == 3


def test_cache_is_bounded_lru_with_injectable_clock():
    clock = Clock()
    cache = SearchCache(ttl=100, max_entries=2, clock=clock)
    cache.put("a", [])
    cache.put("b", [])
    assert cache.get("a") == []  # touch a: b becomes the oldest
    cache.put("c", [])
    assert cache.get("b") is None and cache.get("a") == [] and cache.get("c") == []
    clock.advance(101)
    assert cache.get("a") is None


def test_default_cache_limits():
    assert site_search.CACHE_TTL_SECONDS == 6 * 3600
    assert site_search.CACHE_MAX_ENTRIES == 200
    assert site_search.BREAKER_COOLDOWN_SECONDS == 300
