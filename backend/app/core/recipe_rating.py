"""Slice 3: Pure functions for recipe rating logic."""

from typing import Any


def calculate_rating_stats(ratings: list[int]) -> dict[str, Any]:
    """Calculate rating statistics.

    Args:
        ratings: List of star ratings (1-5)

    Returns:
        dict with average, is_proven, is_rejected, rating_count
    """
    if not ratings:
        return {
            "average": None,
            "rating_count": 0,
            "is_proven": False,
            "is_rejected": False,
        }

    average = sum(ratings) / len(ratings)
    return {
        "average": average,
        "rating_count": len(ratings),
        "is_proven": compute_is_proven(average),
        "is_rejected": compute_is_rejected(average, len(ratings)),
    }


def compute_is_proven(average: float | None) -> bool:
    """A recipe is proven if average rating > 2.0."""
    if average is None:
        return False
    return average > 2.0


def compute_is_rejected(average: float | None, rating_count: int) -> bool:
    """A recipe is rejected if it has ratings and average <= 2.0."""
    if average is None or rating_count == 0:
        return False
    return average <= 2.0
