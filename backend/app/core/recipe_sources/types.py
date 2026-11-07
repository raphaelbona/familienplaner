"""Slice 4: Types for recipe sources."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class RecipeCandidate:
    """A candidate recipe from an external source."""

    title: str
    image_url: str | None
    ingredients: list[str]
    prep_minutes: int | None
    servings: int | None
    source_url: str
    source_name: str
    instructions_summary: str | None = None
    # Full, unstripped ingredient lines ("Schokolade (enthält Haselnüsse)"): used for allergen checks only.
    raw_ingredients: list[str] = field(default_factory=list)
    # Language of title/ingredients ("de" | "en"); never machine-translated, the UI shows an EN badge.
    language: str = "de"


@dataclass
class SourceResult:
    """Candidates plus health: ``failed`` only if the source could not be reached / parsed at all."""

    candidates: list[RecipeCandidate]
    failed: bool = False
    warning: str | None = None  # user-facing text; default "<name> war nicht erreichbar."


class RecipeSource(ABC):
    """Base class for recipe source adapters."""

    display_name: str = "Rezeptquelle"  # shown to users in "<name> war nicht erreichbar."

    def search_with_status(self, ingredients: list[str], limit: int = 5) -> SourceResult:
        """Like ``search`` but also reports failure; adapters override this, ``search`` delegates to it."""
        return SourceResult(self.search(ingredients, limit))

    @abstractmethod
    def search(self, ingredients: list[str], limit: int = 5) -> list[RecipeCandidate]:
        """Search for recipes containing the given ingredients (may be empty)."""
