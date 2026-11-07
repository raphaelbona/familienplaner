"""Slice 4: Recipe source adapters for discovering recipes."""

import logging
import re
import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

from app.core.recipe_sources.budget import DEFAULT_TIME_BUDGET_SECONDS, TimeBudget
from app.core.recipe_sources.de_en_ingredients import to_mealdb_ingredient
from app.core.recipe_sources.health import log_source_failure
from app.core.recipe_sources.types import RecipeCandidate, RecipeSource, SourceResult

__all__ = ["RecipeCandidate", "RecipeSource", "SourceResult", "TheMealDB"]

logger = logging.getLogger(__name__)

MAX_REQUESTS_PER_SEARCH = 6  # filter + lookup calls together: hard cap on the outbound fan-out
MEAL_URL = "https://www.themealdb.com/meal/{}"
_MEAL_ID_RE = re.compile(r"[0-9]{1,12}")
_IMAGE_HOSTS = ("www.themealdb.com", "themealdb.com")


class TheMealDB(RecipeSource):
    """Adapter for TheMealDB (https://www.themealdb.com).

    At most ``MAX_REQUESTS_PER_SEARCH`` HTTP requests and ``time_budget`` seconds in total per
    ``search``; the per-request timeout shrinks with the remaining budget. TheMealDB only knows English
    ingredient names: German terms are mapped (``de_en_ingredients``), unmapped ones are skipped without a
    request. Titles/ingredients stay English (``language="en"``), nothing is machine-translated.
    """

    display_name = "TheMealDB"

    def __init__(
        self,
        time_budget: float = DEFAULT_TIME_BUDGET_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = "https://www.themealdb.com/api/json/v1/1"
        self.timeout = 8.0
        self.time_budget = time_budget
        self._clock = clock

    def search(self, ingredients: list[str], limit: int = 5) -> list[RecipeCandidate]:
        return self.search_with_status(ingredients, limit).candidates

    def search_with_status(self, ingredients: list[str], limit: int = 5) -> SourceResult:
        """Search TheMealDB by (English) ingredient; ``failed`` only if requests were made and none worked."""
        english: list[str] = []
        for term in ingredients:
            name = to_mealdb_ingredient(term)
            if name and name not in english:
                english.append(name)
        ok = [0]
        errors: list[object] = []
        candidates = self._search_english(english, limit, ok, errors)
        failed = bool(errors) and ok[0] == 0
        if failed:
            log_source_failure(self.display_name, errors[-1])
        return SourceResult(candidates, failed=failed)

    def _search_english(
        self, ingredients: list[str], limit: int, ok: list[int], errors: list[object]
    ) -> list[RecipeCandidate]:
        candidates: list[RecipeCandidate] = []
        seen_ids: set[str] = set()
        headers = {"User-Agent": "Familienplaner/1.0"}
        budget = TimeBudget(self.time_budget, self._clock)
        requests = 0

        for ingredient in ingredients[:5]:  # bound the number of outbound calls
            if requests >= MAX_REQUESTS_PER_SEARCH or budget.expired():
                break
            try:
                requests += 1
                resp = httpx.get(
                    f"{self.base_url}/filter.php",
                    params={"i": ingredient},
                    timeout=budget.timeout(self.timeout),
                    headers=headers,
                )
                resp.raise_for_status()
                meals = resp.json().get("meals") or []
                ok[0] += 1

                for meal in meals[:3]:  # limit per ingredient
                    meal_id = meal.get("idMeal") if isinstance(meal, dict) else None
                    if not isinstance(meal_id, str) or not _MEAL_ID_RE.fullmatch(meal_id) or meal_id in seen_ids:
                        continue
                    if requests >= MAX_REQUESTS_PER_SEARCH or budget.expired():
                        return candidates[:limit]
                    seen_ids.add(meal_id)

                    requests += 1
                    detail_resp = httpx.get(
                        f"{self.base_url}/lookup.php",
                        params={"i": meal_id},
                        timeout=budget.timeout(self.timeout),
                        headers=headers,
                    )
                    detail_resp.raise_for_status()
                    ok[0] += 1
                    detail_meals = detail_resp.json().get("meals")
                    if not detail_meals:
                        continue

                    full_meal = detail_meals[0]
                    detail_id = full_meal.get("idMeal")
                    if not isinstance(detail_id, str) or not _MEAL_ID_RE.fullmatch(detail_id):
                        continue  # never build a URL from a non-numeric id
                    candidates.append(
                        RecipeCandidate(
                            title=full_meal.get("strMeal") or "Unknown",
                            image_url=self._trusted_image(full_meal.get("strMealThumb")),
                            ingredients=self._extract_ingredients(full_meal),
                            prep_minutes=None,  # TheMealDB does not provide prep time
                            servings=None,
                            source_url=MEAL_URL.format(detail_id),
                            source_name="TheMealDB",
                            language="en",
                        )
                    )
                    if len(candidates) >= limit:
                        return candidates

            except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError) as exc:
                logger.debug("TheMealDB lookup failed for %r: %s", ingredient, type(exc).__name__)
                errors.append(exc)
                continue

        return candidates[:limit]

    @staticmethod
    def _trusted_image(url: object) -> str | None:
        """https image on the TheMealDB host only (anything else is dropped)."""
        if not isinstance(url, str) or len(url) > 500:
            return None
        try:
            parsed = urlparse(url)
        except ValueError:
            return None
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        return url if parsed.hostname in _IMAGE_HOSTS else None

    def _extract_ingredients(self, meal: dict) -> list[str]:
        """Extract ingredient names from meal dict (TheMealDB has up to 20)."""
        names = []
        for i in range(1, 21):
            value = meal.get(f"strIngredient{i}")
            if isinstance(value, str) and value.strip():
                names.append(value.strip())
        return names
