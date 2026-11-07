"""/recipes/discover abuse protection: rate limit, concurrency cap, deadlines, bounded fan-out and threads."""

import socket
import threading
import time
from datetime import date

import httpx
import pytest

from app.core.config import Settings
from app.models.user import UserRole
from tests.food_helpers import FakeSource, add_member, cand, register_family

THIS_YEAR = date.today().year


class FakeClock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _default_settings(monkeypatch):
    from app.core import recipe_discovery

    monkeypatch.setattr(recipe_discovery, "get_settings", lambda: Settings(llm_recipe_check_enabled=False))
    monkeypatch.setattr(recipe_discovery, "build_default_sources", lambda: [FakeSource([cand("Reis", ["Reis"])])])


# ---------------------------------------------------------------- limiter unit tests


def test_limiter_enforces_min_interval_and_reports_retry_after():
    from app.core.discover_limits import DiscoverRateLimited, DiscoverRateLimiter

    clock = FakeClock()
    limiter = DiscoverRateLimiter(min_interval=10, daily_cap=100, clock=clock)
    limiter.check("fam-1")
    clock.advance(3)
    with pytest.raises(DiscoverRateLimited) as exc:
        limiter.check("fam-1")
    assert exc.value.retry_after == 7
    clock.advance(7)
    limiter.check("fam-1")


def test_limiter_is_per_family():
    from app.core.discover_limits import DiscoverRateLimiter

    limiter = DiscoverRateLimiter(min_interval=10, daily_cap=100, clock=FakeClock())
    limiter.check("fam-1")
    limiter.check("fam-2")  # a different family is not throttled


def test_limiter_daily_cap_and_rollover():
    from app.core.discover_limits import DiscoverRateLimited, DiscoverRateLimiter

    clock = FakeClock(now=86400 * 100 + 100.0)  # early in a UTC day
    limiter = DiscoverRateLimiter(min_interval=1, daily_cap=3, clock=clock)
    for _ in range(3):
        limiter.check("fam-1")
        clock.advance(2)
    with pytest.raises(DiscoverRateLimited) as exc:
        limiter.check("fam-1")
    assert exc.value.retry_after > 60  # comes back tomorrow
    clock.advance(86400)
    limiter.check("fam-1")


def test_rejected_attempts_do_not_extend_the_window_or_count():
    from app.core.discover_limits import DiscoverRateLimited, DiscoverRateLimiter

    clock = FakeClock()
    limiter = DiscoverRateLimiter(min_interval=10, daily_cap=2, clock=clock)
    limiter.check("f")
    for _ in range(5):
        clock.advance(1)
        with pytest.raises(DiscoverRateLimited):
            limiter.check("f")
    clock.advance(10)
    limiter.check("f")  # 2nd accepted call -> cap not consumed by the 5 rejected ones


def test_limiter_memory_is_bounded():
    from app.core.discover_limits import DiscoverRateLimiter

    limiter = DiscoverRateLimiter(min_interval=0, daily_cap=100, clock=FakeClock(), max_families=50)
    for i in range(500):
        limiter.check(f"fam-{i}")
    assert limiter.tracked_families <= 50


def test_slots_are_limited_and_released():
    from app.core.discover_limits import DiscoverSlots

    slots = DiscoverSlots(2)
    assert slots.try_acquire() and slots.try_acquire()
    assert slots.try_acquire() is False
    slots.release()
    assert slots.try_acquire()


# ---------------------------------------------------------------- endpoint behaviour


def _family(client, db, email):
    headers, fam = register_family(client, email)
    child = add_member(db, fam, UserRole.CHILD, "Anna", THIS_YEAR - 8)
    return headers, child


def _post(client, headers, child):
    return client.post("/recipes/discover", headers=headers, json={"eater_user_ids": [str(child.id)]})


def test_discover_endpoint_returns_429_with_retry_after(client, db_session, monkeypatch):
    from app.api import recipes
    from app.core.discover_limits import DiscoverRateLimiter

    clock = FakeClock()
    monkeypatch.setattr(recipes, "discover_limiter", DiscoverRateLimiter(min_interval=10, daily_cap=100, clock=clock))
    headers, child = _family(client, db_session, "lim-429@example.ch")
    assert _post(client, headers, child).status_code == 200
    resp = _post(client, headers, child)
    assert resp.status_code == 429
    assert 1 <= int(resp.headers["Retry-After"]) <= 10
    clock.advance(10)
    assert _post(client, headers, child).status_code == 200


def test_discover_endpoint_daily_cap(client, db_session, monkeypatch):
    from app.api import recipes
    from app.core.discover_limits import DiscoverRateLimiter

    clock = FakeClock(now=86400 * 200 + 5.0)
    monkeypatch.setattr(recipes, "discover_limiter", DiscoverRateLimiter(min_interval=1, daily_cap=2, clock=clock))
    headers, child = _family(client, db_session, "lim-daily@example.ch")
    for _ in range(2):
        assert _post(client, headers, child).status_code == 200
        clock.advance(5)
    resp = _post(client, headers, child)
    assert resp.status_code == 429 and int(resp.headers["Retry-After"]) > 60


def test_rate_limit_is_checked_after_authorization(client, db_session, monkeypatch):
    """A child (403) must not burn the family's quota, and other families are unaffected."""
    from app.api import recipes
    from app.core.discover_limits import DiscoverRateLimiter
    from tests.food_helpers import pin_headers

    limiter = DiscoverRateLimiter(min_interval=10, daily_cap=100, clock=FakeClock())
    monkeypatch.setattr(recipes, "discover_limiter", limiter)
    headers, fam = register_family(client, "lim-auth@example.ch")
    child = add_member(db_session, fam, UserRole.CHILD, "Anna", THIS_YEAR - 8)
    child_headers = pin_headers(client, fam, child)
    assert _post(client, child_headers, child).status_code == 403
    assert _post(client, headers, child).status_code == 200


def test_discover_endpoint_503_when_all_slots_busy_and_slot_is_released(client, db_session, monkeypatch):
    from app.api import recipes
    from app.core.discover_limits import DiscoverRateLimiter, DiscoverSlots

    slots = DiscoverSlots(1)
    monkeypatch.setattr(recipes, "discover_slots", slots)
    limiter = DiscoverRateLimiter(min_interval=0, daily_cap=100, clock=FakeClock())
    monkeypatch.setattr(recipes, "discover_limiter", limiter)
    headers, child = _family(client, db_session, "lim-slots@example.ch")
    assert slots.try_acquire()  # another run is in flight
    resp = _post(client, headers, child)
    assert resp.status_code == 503 and int(resp.headers["Retry-After"]) >= 1
    slots.release()
    assert _post(client, headers, child).status_code == 200
    assert slots.try_acquire()  # the finished request gave its slot back
    slots.release()


def test_slot_is_released_when_discovery_crashes(client, db_session, monkeypatch):
    from app.api import recipes
    from app.core.discover_limits import DiscoverRateLimiter, DiscoverSlots

    slots = DiscoverSlots(1)
    monkeypatch.setattr(recipes, "discover_slots", slots)
    limiter = DiscoverRateLimiter(min_interval=0, daily_cap=100, clock=FakeClock())
    monkeypatch.setattr(recipes, "discover_limiter", limiter)

    def boom(self, *a, **k):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(recipes.RecipeDiscoveryService, "discover_with_candidates", boom)
    headers, child = _family(client, db_session, "lim-crash@example.ch")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as tolerant:  # dependency overrides are app-global
        resp = tolerant.post("/recipes/discover", headers=headers, json={"eater_user_ids": [str(child.id)]})
    assert resp.status_code == 500
    assert slots.try_acquire()


def test_total_discover_budget_is_at_most_15_seconds():
    from app.core import recipe_discovery
    from app.core.recipe_sources import TheMealDB
    from app.core.recipe_sources.chefkoch import ChefkochSearchSource

    assert recipe_discovery.SOURCE_TIMEOUT_SECONDS <= 15
    assert TheMealDB().time_budget <= recipe_discovery.SOURCE_TIMEOUT_SECONDS
    assert ChefkochSearchSource().time_budget <= recipe_discovery.SOURCE_TIMEOUT_SECONDS


# ---------------------------------------------------------------- TheMealDB: fan-out cap + deadline


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


def _mealdb_get(calls, clock=None, per_call=0.0):
    def get(url, *args, **kwargs):
        calls.append((url, kwargs.get("timeout")))
        if clock is not None:
            clock.advance(per_call)
        if "filter.php" in url:
            base = len(calls) * 10
            return _Resp({"meals": [{"idMeal": str(base + i), "strMeal": f"M{base + i}"} for i in range(5)]})
        meal_id = kwargs["params"]["i"]
        return _Resp({"meals": [{"idMeal": meal_id, "strMeal": f"Meal {meal_id}", "strIngredient1": "Reis"}]})

    return get


def test_themealdb_fanout_is_capped(monkeypatch):
    from app.core.recipe_sources import TheMealDB

    calls: list = []
    monkeypatch.setattr(httpx, "get", _mealdb_get(calls))
    TheMealDB().search(["reis", "huhn", "lachs", "milch", "butter"], limit=50)
    assert 0 < len(calls) <= 6


def test_themealdb_stops_at_deadline_and_shortens_timeouts(monkeypatch):
    from app.core.recipe_sources import TheMealDB

    calls: list = []
    clock = FakeClock()
    monkeypatch.setattr(httpx, "get", _mealdb_get(calls, clock, per_call=4.0))
    src = TheMealDB(time_budget=10.0, clock=clock)
    src.search(["reis", "huhn", "lachs", "milch", "butter"], limit=50)
    assert len(calls) == 3  # 4s each, budget 10s: 3rd call starts at t=8, then the budget is gone
    assert all(t is not None and t <= 10.0 for _, t in calls)
    assert calls[2][1] == pytest.approx(2.0)  # only 2 s were left for the last request


def test_themealdb_skips_candidates_without_numeric_id(monkeypatch):
    from app.core.recipe_sources import TheMealDB

    def get(url, *args, **kwargs):
        if "filter.php" in url:
            return _Resp({"meals": [{"idMeal": "../evil"}, {"idMeal": "123"}]})
        meal_id = kwargs["params"]["i"]
        return _Resp({"meals": [{"idMeal": meal_id, "strMeal": "X", "strIngredient1": "Reis"}]})

    monkeypatch.setattr(httpx, "get", get)
    results = TheMealDB().search(["reis"], limit=5)
    assert [r.source_url for r in results] == ["https://www.themealdb.com/meal/123"]


def test_themealdb_detail_with_bad_id_or_image_is_sanitised(monkeypatch):
    from app.core.recipe_sources import TheMealDB

    def get(url, *args, **kwargs):
        if "filter.php" in url:
            return _Resp({"meals": [{"idMeal": "1"}, {"idMeal": "2"}, {"idMeal": "3"}]})
        meal_id = kwargs["params"]["i"]
        detail = {"idMeal": meal_id, "strMeal": f"M{meal_id}", "strIngredient1": "Reis"}
        if meal_id == "1":
            detail["idMeal"] = "1; drop"  # detail disagrees / not numeric -> skipped
        if meal_id == "2":
            detail["strMealThumb"] = "http://www.themealdb.com/images/x.jpg"  # http -> dropped
        if meal_id == "3":
            detail["strMealThumb"] = "https://evil.example/x.jpg"  # foreign host -> dropped
        return _Resp({"meals": [detail]})

    monkeypatch.setattr(httpx, "get", get)
    results = TheMealDB().search(["reis"], limit=5)
    by_url = {r.source_url: r for r in results}
    assert "https://www.themealdb.com/meal/1; drop" not in by_url
    assert set(by_url) == {"https://www.themealdb.com/meal/2", "https://www.themealdb.com/meal/3"}
    assert all(r.image_url is None for r in results)


def test_themealdb_keeps_https_image_on_trusted_host(monkeypatch):
    from app.core.recipe_sources import TheMealDB

    def get(url, *args, **kwargs):
        if "filter.php" in url:
            return _Resp({"meals": [{"idMeal": "7"}]})
        return _Resp(
            {
                "meals": [
                    {
                        "idMeal": "7",
                        "strMeal": "X",
                        "strMealThumb": "https://www.themealdb.com/images/media/meals/x.jpg",
                        "strIngredient1": "Reis",
                    }
                ]
            }
        )

    monkeypatch.setattr(httpx, "get", get)
    assert TheMealDB().search(["reis"])[0].image_url == "https://www.themealdb.com/images/media/meals/x.jpg"


# ---------------------------------------------------------------- web source: total deadline


@pytest.fixture()
def _no_dns(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    )


def _web(handler, monkeypatch, clock, budget):
    from app.core.recipe_sources import site_search
    from app.core.recipe_sources.chefkoch import ChefkochSearchSource

    settings = Settings(recipe_search_enabled=True)
    monkeypatch.setattr(site_search, "get_settings", lambda: settings)
    return ChefkochSearchSource(transport=httpx.MockTransport(handler), time_budget=budget, clock=clock)


def test_web_source_stops_issuing_requests_after_deadline(monkeypatch, _no_dns):
    clock = FakeClock()
    seen = []
    links = "".join(f'<a href="/rezepte/{1274521232808420 + i}/Moehren-{i}.html">r</a>' for i in range(1, 5))

    def handler(request):
        seen.append(request)
        clock.advance(5.0)
        body = f"<html>{links}</html>" if request.url.path.startswith("/rs/") else "<html></html>"
        return httpx.Response(200, text=body)

    src = _web(handler, monkeypatch, clock, budget=12.0)
    src.search(["a", "b"], limit=10)
    assert len(seen) == 3  # t=0 search, t=5 and t=10 recipe pages; the third page would start at t=15 > budget


def test_web_source_download_aborts_slow_drip_body(monkeypatch, _no_dns):
    clock = FakeClock()
    fetched_chunks = []

    def slow_body():
        for i in range(100):
            fetched_chunks.append(i)
            clock.advance(1.0)
            yield b"x" * 10

    def handler(request):
        if request.url.path.startswith("/rs/s0/"):
            return httpx.Response(200, text='<a href="/rezepte/1274521232808421/Moehren.html">r</a>')
        return httpx.Response(200, content=slow_body())

    src = _web(handler, monkeypatch, clock, budget=8.0)
    assert src.search(["a"], limit=5) == []
    assert len(fetched_chunks) <= 10  # aborted by the total deadline, not after 100 chunks


def test_web_source_passes_remaining_time_as_request_timeout(monkeypatch, _no_dns):
    clock = FakeClock()
    timeouts = []

    def handler(request):
        timeouts.append(request.extensions.get("timeout"))
        clock.advance(6.0)
        return httpx.Response(200, text='<a href="/rezepte/1274521232808421/Moehren.html">r</a>')

    src = _web(handler, monkeypatch, clock, budget=10.0)
    src.search(["a"], limit=5)
    assert timeouts[0]["read"] <= 8.0
    assert timeouts[1]["read"] == pytest.approx(4.0)


# ---------------------------------------------------------------- source pool: no pile-up, queued work is cancelled


class _Blocking:
    """Source that holds its worker thread until ``release`` is set."""

    display_name = "Blockiert"

    def __init__(self, release: threading.Event):
        self.release = release
        self.started = threading.Event()

    def search(self, ingredients, limit=5):
        self.started.set()
        self.release.wait(timeout=5)
        return []


class _Recording:
    display_name = "Wartend"

    def __init__(self):
        self.ran = threading.Event()

    def search(self, ingredients, limit=5):
        self.ran.set()
        return []


def test_queued_source_work_is_cancelled_after_timeout(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from app.core import recipe_discovery
    from app.core.recipe_discovery import RecipeDiscoveryService

    pool = ThreadPoolExecutor(max_workers=1)  # the blocker occupies the only worker: the 2nd source stays queued
    monkeypatch.setattr(recipe_discovery, "_pool", pool)
    monkeypatch.setattr(recipe_discovery, "SOURCE_TIMEOUT_SECONDS", 0.2)
    release = threading.Event()
    blocker, queued = _Blocking(release), _Recording()
    try:
        _, warnings = RecipeDiscoveryService([blocker, queued])._run_sources(["reis"])
        assert blocker.started.wait(1)
        release.set()  # the worker becomes free: cancelled work must NOT start now
        time.sleep(0.3)
        assert not queued.ran.is_set()
        assert warnings == ["Blockiert war nicht erreichbar.", "Wartend war nicht erreichbar."]
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_source_threads_cannot_pile_up(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from app.core import recipe_discovery
    from app.core.recipe_discovery import SOURCE_POOL_WORKERS, RecipeDiscoveryService

    pool = ThreadPoolExecutor(max_workers=SOURCE_POOL_WORKERS, thread_name_prefix="recipe-source-test")
    monkeypatch.setattr(recipe_discovery, "_pool", pool)
    monkeypatch.setattr(recipe_discovery, "SOURCE_TIMEOUT_SECONDS", 0.02)
    release = threading.Event()
    try:
        for _ in range(SOURCE_POOL_WORKERS * 4):  # many discover calls while every source hangs
            RecipeDiscoveryService([_Blocking(release), _Blocking(release)])._run_sources(["reis"])
        workers = [t for t in threading.enumerate() if t.name.startswith("recipe-source-test")]
        assert len(workers) <= SOURCE_POOL_WORKERS
    finally:
        release.set()
        pool.shutdown(wait=True)
