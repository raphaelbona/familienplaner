"""Generic recipe-site search source: site search page -> recipe links -> JSON-LD recipe pages.

``SiteSearchSource`` holds everything security relevant, a subclass only supplies the host,
the search-URL builder and the exact recipe-link pattern (see ``chefkoch.py``).

Security properties: https only on port 443, no credentials, host must be the allowlisted domain
(or a real subdomain), the SSRF guard runs before every fetch, redirects are never followed
automatically (at most ``MAX_REDIRECTS`` manual hops, each re-validated against the same policy),
only HTTP 200 is parsed, the body is size-capped while streaming, and links from search results are
only used if they match the site's exact recipe pattern. Outbound requests per ``search`` call and
the total wall-clock time (``time_budget``) are bounded.
"""

import copy
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

from app.core.config import get_settings
from app.core.network_guard import validate_external_server_url
from app.core.recipe_sources.budget import DEFAULT_TIME_BUDGET_SECONDS, TimeBudget
from app.core.recipe_sources.health import log_source_failure, overloaded_warning
from app.core.recipe_sources.json_ld_parser import (
    extract_json_ld_from_html,
    extract_og_image,
    find_recipes_in_json_ld,
)
from app.core.recipe_sources.types import RecipeCandidate, RecipeSource, SourceResult
from app.core.recipe_sources.url_policy import host_matches

logger = logging.getLogger(__name__)

MAX_TERMS_PER_CALL = 1  # discovery passes terms ordered by weight: only the most liked one is searched
MAX_RECIPE_PAGES_PER_TERM = 3  # => at most 1 search page + 3 recipe pages per discover call
MIN_REQUEST_INTERVAL = 1.0  # seconds between two outbound requests to the same host (politeness)
BREAKER_COOLDOWN_SECONDS = 300.0  # source is skipped without any request after a 429/503
MAX_RETRY_AFTER_SECONDS = 300.0
CACHE_TTL_SECONDS = 6 * 3600.0
CACHE_MAX_ENTRIES = 200
MAX_REDIRECTS = 2
MAX_TERM_LENGTH = 60
MAX_BODY_BYTES = int(1.5 * 1024 * 1024)
USER_AGENT = "FamilienplanerBot/1.0 (family meal planner; recipe suggestions for private use)"
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class _AnchorExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if isinstance(href, str):
                self.hrefs.append(href)


class SiteGate:
    """Per-host politeness: minimum interval between requests plus a circuit breaker (thread safe)."""

    def __init__(
        self,
        min_interval: float | None = None,
        cooldown: float = BREAKER_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = MIN_REQUEST_INTERVAL if min_interval is None else min_interval
        self.cooldown = cooldown
        self.clock = clock
        self.sleeper = sleeper
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self._breaker_until = 0.0

    def reserve(self, max_wait: float) -> float | None:
        """Seconds to wait before the next request, or None if that would exceed ``max_wait`` (do not sleep)."""
        with self._lock:
            now = self.clock()
            start = max(now, self._next_slot)
            wait = start - now
            if wait > max_wait:
                return None
            self._next_slot = start + self.min_interval
            return wait

    def breaker_open(self) -> bool:
        with self._lock:
            return self.clock() < self._breaker_until

    def open_breaker(self, retry_after: float | None = None) -> None:
        seconds = max(self.cooldown, min(retry_after or 0.0, MAX_RETRY_AFTER_SECONDS))
        with self._lock:
            self._breaker_until = max(self._breaker_until, self.clock() + seconds)


class SearchCache:
    """Successful candidate lists per normalised term: bounded LRU with TTL (thread safe)."""

    def __init__(
        self,
        ttl: float = CACHE_TTL_SECONDS,
        max_entries: int = CACHE_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self.clock = clock
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[float, list[RecipeCandidate]]] = OrderedDict()

    def get(self, key: str) -> list[RecipeCandidate] | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if self.clock() - entry[0] >= self.ttl:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return copy.deepcopy(entry[1])

    def put(self, key: str, candidates: list[RecipeCandidate]) -> None:
        with self._lock:
            self._data[key] = (self.clock(), copy.deepcopy(candidates))
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)


# Sources are created per discover call, politeness/cache state must outlive them: one per site (domain).
_shared_lock = threading.Lock()
_gates: dict[str, SiteGate] = {}
_caches: dict[str, SearchCache] = {}


def shared_gate(domain: str) -> SiteGate:
    with _shared_lock:
        return _gates.setdefault(domain, SiteGate())


def shared_cache(domain: str) -> SearchCache:
    with _shared_lock:
        return _caches.setdefault(domain, SearchCache())


def reset_shared_state() -> None:
    """For tests: forget breakers, request pacing and cached results."""
    with _shared_lock:
        _gates.clear()
        _caches.clear()


def _retry_after_seconds(value: str | None) -> float | None:
    if value and value.strip().isdigit():
        return min(float(value.strip()), MAX_RETRY_AFTER_SECONDS)
    return None


@dataclass
class _Run:
    """Per-``search`` state: budget plus request outcomes (kept off the instance: thread safe)."""

    budget: TimeBudget
    attempted: int = 0
    succeeded: int = 0
    last_error: object | None = None
    overloaded: bool = False

    @property
    def failed(self) -> bool:
        return self.attempted > 0 and self.succeeded == 0


class SiteSearchSource(RecipeSource):
    """Base class: subclasses set ``domain``, ``display_name``, ``recipe_link_re`` and ``search_url``."""

    domain: str = ""
    recipe_link_re: re.Pattern[str]
    search_path_re: re.Pattern[str]  # the only search URL shape we ever fetch (robots.txt friendly)

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        time_budget: float = DEFAULT_TIME_BUDGET_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        gate: SiteGate | None = None,
        cache: SearchCache | None = None,
    ) -> None:
        self._gate = gate or shared_gate(self.domain)
        self._cache = cache or shared_cache(self.domain)
        self.timeout = 8.0
        self.time_budget = time_budget  # total wall-clock seconds per ``search`` (httpx timeouts are per phase)
        self._clock = clock
        self.max_body_size = MAX_BODY_BYTES
        self._transport = transport

    def search_url(self, term: str) -> str:
        raise NotImplementedError

    def search(self, ingredients: list[str], limit: int = 5) -> list[RecipeCandidate]:
        return self.search_with_status(ingredients, limit).candidates

    def search_with_status(self, ingredients: list[str], limit: int = 5) -> SourceResult:
        if not get_settings().recipe_search_enabled:
            return SourceResult([])
        terms = [t.strip() for t in ingredients if t and t.strip()][:MAX_TERMS_PER_CALL]
        if not terms:
            return SourceResult([])

        candidates: list[RecipeCandidate] = []
        seen_urls: set[str] = set()
        run = _Run(TimeBudget(self.time_budget, self._clock))
        with self._client() as client:
            for term in terms:
                if run.budget.expired() or len(candidates) >= limit or run.overloaded:
                    break
                key = term[:MAX_TERM_LENGTH].strip().lower()
                found = self._cache.get(key)
                if found is None:
                    if self._gate.breaker_open():  # cool-down: no request at all, same warning
                        run.overloaded = True
                        break
                    errors_before = run.last_error
                    try:
                        found = self._search_term(client, term[:MAX_TERM_LENGTH], run)
                    except Exception as exc:  # failure isolation: one bad term never kills the call
                        run.attempted += 1
                        run.last_error = exc
                        continue
                    if run.last_error is errors_before and not run.overloaded and not run.failed:
                        self._cache.put(key, found)  # failures / 429 are never cached as "nothing found"
                for recipe in found:
                    if recipe.source_url not in seen_urls:
                        seen_urls.add(recipe.source_url)
                        candidates.append(recipe)
        if run.overloaded:
            log_source_failure(self.display_name, "overloaded (429/503) or cooling down")
            return SourceResult(
                candidates[:limit], failed=True, warning=overloaded_warning(self.display_name)
            )
        if run.failed:
            log_source_failure(self.display_name, run.last_error or "no successful response")
        return SourceResult(candidates[:limit], failed=run.failed)

    # -- internals -----------------------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(
            transport=self._transport,
            timeout=self.timeout,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "de"},
        )

    def _search_term(self, client: httpx.Client, term: str, run: _Run) -> list[RecipeCandidate]:
        url = self.search_url(term)
        html = self._fetch(client, url, run, self.search_path_re)
        if html is None:
            return []
        parser = _AnchorExtractor()
        parser.feed(html)
        links: list[str] = []
        for href in parser.hrefs:
            link = self._canonical_recipe_link(urljoin(url, href))
            if link and link not in links:
                links.append(link)

        results = []
        for link in links[:MAX_RECIPE_PAGES_PER_TERM]:
            if run.budget.expired():
                break
            recipe = self._fetch_and_parse(link, run, client)
            if recipe:
                results.append(recipe)
        return results

    def _canonical_recipe_link(self, url: str) -> str | None:
        """Query/fragment stripped; only the exact recipe-page shape on the allowlisted host qualifies."""
        url = urldefrag(url)[0].split("?", 1)[0]
        return url if self._url_allowed(url, self.recipe_link_re) else None

    def _url_allowed(self, url: str, path_re: re.Pattern[str] | None) -> bool:
        try:
            parsed = urlparse(url)
            port = parsed.port
        except ValueError:
            return False
        if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443):
            return False
        if not host_matches(parsed.hostname, self.domain):
            return False
        return path_re is None or path_re.fullmatch(parsed.path) is not None

    def _fetch_and_parse(
        self, url: str, run: _Run | None = None, client: httpx.Client | None = None
    ) -> RecipeCandidate | None:
        """Fetch one recipe page and parse its JSON-LD recipe; None on any policy violation or failure."""
        run = run or _Run(TimeBudget(self.time_budget, self._clock))
        try:
            if not self._url_allowed(url, self.recipe_link_re):
                return None
            if client is None:
                with self._client() as own_client:
                    html = self._fetch(own_client, url, run, self.recipe_link_re)
            else:
                html = self._fetch(client, url, run, self.recipe_link_re)
            if html is None:
                return None
            recipes = find_recipes_in_json_ld(extract_json_ld_from_html(html))
            if not recipes:
                return None  # a page without recipe JSON-LD is simply skipped
            recipe = recipes[0]
            if recipe.image_url is None:
                recipe.image_url = extract_og_image(html)
            recipe.source_url = url
            recipe.source_name = urlparse(url).hostname or self.domain
            recipe.language = "de"
            return recipe
        except Exception as exc:
            run.last_error = exc
            logger.debug("Parse failed for %s: %s", url, type(exc).__name__)
            return None

    def _fetch(self, client: httpx.Client, url: str, run: _Run, path_re: re.Pattern[str] | None) -> str | None:
        """GET with manual, re-validated redirects. Network errors and non-200 count as source failure."""
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            if run.overloaded or run.budget.expired() or not self._url_allowed(current, path_re):
                return None
            validate_external_server_url(current, get_settings())
            wait = self._gate.reserve(run.budget.remaining())
            if wait is None:  # waiting would exceed the total budget: stop instead of sleeping
                return None
            if wait > 0:
                self._gate.sleeper(wait)
                if run.budget.expired():
                    return None
            run.attempted += 1
            try:
                with client.stream("GET", current, timeout=run.budget.timeout(self.timeout)) as resp:
                    if resp.status_code in _REDIRECT_STATUSES:
                        location = resp.headers.get("location")
                        if not location:
                            run.last_error = f"HTTP {resp.status_code}"
                            return None
                        run.succeeded += 1  # the server answered properly
                        current = urljoin(current, location)
                        continue
                    if resp.status_code in (429, 503):
                        run.overloaded = True
                        run.last_error = f"HTTP {resp.status_code}"
                        self._gate.open_breaker(_retry_after_seconds(resp.headers.get("retry-after")))
                        return None
                    if resp.status_code != 200:
                        run.last_error = f"HTTP {resp.status_code}"
                        return None
                    run.succeeded += 1
                    return self._read_body(resp, run.budget)
            except httpx.HTTPError as exc:
                run.last_error = exc
                return None
        return None  # too many redirects

    def _read_body(self, resp: httpx.Response, budget: TimeBudget) -> str | None:
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_body_size:
            return None
        chunks: list[bytes] = []
        received = 0
        for chunk in resp.iter_bytes():
            if budget.expired():  # slow-drip body: the per-phase read timeout never fires
                return None
            received += len(chunk)
            if received > self.max_body_size:
                return None
            chunks.append(chunk)
        # Decode once (never per chunk: multi-byte characters would break at chunk boundaries).
        data = b"".join(chunks)
        try:
            return data.decode(resp.charset_encoding or "utf-8", errors="replace")
        except LookupError:
            return data.decode("utf-8", errors="replace")
