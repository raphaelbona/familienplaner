"""Total wall-clock budget for one source ``search`` call (httpx timeouts are per phase only)."""

import time
from collections.abc import Callable

# Per-source budget: clearly below recipe_discovery.SOURCE_TIMEOUT_SECONDS (12 s pool wait) so a slow source
# returns its partial candidates itself instead of being cut off and reported as a timeout.
DEFAULT_TIME_BUDGET_SECONDS = 9.0


class TimeBudget:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._deadline = clock() + seconds

    def remaining(self) -> float:
        return max(0.0, self._deadline - self._clock())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def timeout(self, per_request: float) -> float:
        """Per-request timeout: the normal one, but never longer than what is left of the budget."""
        return min(per_request, self.remaining())
