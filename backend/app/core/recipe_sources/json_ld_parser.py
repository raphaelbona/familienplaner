"""Slice 4: JSON-LD recipe schema parser."""

import json
import re
from html.parser import HTMLParser
from typing import Any

from app.core.recipe_sources.types import RecipeCandidate
from app.core.recipe_sources.url_policy import is_https_url

_MAX_INGREDIENTS = 60
_MAX_RAW_LINE = 300
_UNITS = (
    "kg|g|mg|l|dl|cl|ml|el|tl|prise|prisen|stk|stück|stueck|bund|zehe|zehen|dose|dosen|pck|pkg|packung|"
    "tbsp|tsp|cup|cups|oz|lb|lbs|pinch|clove|cloves|can|cans|bunch|slice|slices"
)
_QUANTITY_RE = re.compile(
    rf"^\s*(?:[\d½¼¾⅓⅔][\d.,/½¼¾⅓⅔\s\-–]*)?\s*(?:(?:{_UNITS})\b\.?\s+)?", re.IGNORECASE
)


class JsonLdParser(HTMLParser):
    """Extract JSON-LD script tags from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.json_ld_blocks: list[str] = []
        self.current_script: str | None = None
        self.in_json_ld = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "script":
            attrs_dict = dict(attrs)
            if (attrs_dict.get("type") or "").strip().lower() == "application/ld+json":
                self.in_json_ld = True
                self.current_script = ""

    def handle_data(self, data: str) -> None:
        if self.in_json_ld and self.current_script is not None:
            self.current_script += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.in_json_ld:
            self.in_json_ld = False
            if self.current_script:
                self.json_ld_blocks.append(self.current_script)
            self.current_script = None


def parse_json_ld_recipe(data: dict) -> RecipeCandidate | None:
    """Parse a JSON-LD Recipe object; None if it has no name or no ingredients."""
    try:
        title = data.get("name")
        if not isinstance(title, str) or not title.strip():
            return None

        raw_ingredients = _extract_raw_lines(data.get("recipeIngredient", []))
        ingredients = _extract_ingredients(data.get("recipeIngredient", []))
        if not ingredients:
            return None

        prep = _parse_iso8601_duration(data.get("prepTime"))
        cook = _parse_iso8601_duration(data.get("cookTime"))
        if prep is not None or cook is not None:
            total_minutes = (prep or 0) + (cook or 0)
        else:
            total_minutes = _parse_iso8601_duration(data.get("totalTime"))

        return RecipeCandidate(
            title=title.strip(),
            image_url=_extract_image_url(data.get("image")),
            ingredients=ingredients,
            prep_minutes=total_minutes,
            servings=_extract_servings(data.get("recipeYield")),
            source_url=data.get("url") if isinstance(data.get("url"), str) else "",
            source_name="Web Recipe",
            instructions_summary=_extract_instructions_summary(data.get("recipeInstructions")),
            raw_ingredients=raw_ingredients,
        )
    except Exception:
        return None


def _extract_servings(yield_data: Any) -> int | None:
    if isinstance(yield_data, list) and yield_data:
        yield_data = yield_data[0]
    if isinstance(yield_data, int | float) and not isinstance(yield_data, bool):
        return int(yield_data) or None
    if isinstance(yield_data, str):
        match = re.search(r"(\d+)", yield_data)
        if match:
            return int(match.group(1)) or None
    return None


def _extract_image_url(image_data: Any) -> str | None:
    """Extract an https image URL from string / list / object formats."""
    if isinstance(image_data, list):
        image_data = image_data[0] if image_data else None
    if isinstance(image_data, dict):
        image_data = image_data.get("url")
    if isinstance(image_data, str) and is_https_url(image_data):
        return image_data
    return None


def _extract_raw_lines(recipe_ingredients: Any) -> list[str]:
    """Unstripped ingredient lines (for allergen checks): the parenthetical/comma text is kept."""
    lines: list[str] = []
    if isinstance(recipe_ingredients, list):
        for item in recipe_ingredients[:_MAX_INGREDIENTS]:
            raw = item if isinstance(item, str) else item.get("name") if isinstance(item, dict) else None
            if isinstance(raw, str) and raw.strip():
                lines.append(raw.strip()[:_MAX_RAW_LINE])
    return lines


def _extract_ingredients(recipe_ingredients: Any) -> list[str]:
    names: list[str] = []
    if isinstance(recipe_ingredients, list):
        for item in recipe_ingredients:
            raw = item if isinstance(item, str) else item.get("name") if isinstance(item, dict) else None
            if isinstance(raw, str):
                name = _extract_ingredient_name(raw)
                if name:
                    names.append(name)
            if len(names) >= _MAX_INGREDIENTS:
                break
    return names


def _extract_ingredient_name(ingredient_str: str) -> str | None:
    """'1,5 kg Tomaten (reif)' -> 'Tomaten'; '4 Eggs' -> 'Eggs'."""
    text = _QUANTITY_RE.sub("", ingredient_str.strip(), count=1)
    text = re.sub(r"\s*\([^)]*\)", "", text)
    text = text.split(",")[0].strip()
    return text or None


def _parse_iso8601_duration(duration_str: Any) -> int | None:
    """Parse ISO8601 durations such as PT30M, PT1H15M or P0DT45M to minutes."""
    if not isinstance(duration_str, str):
        return None
    match = re.fullmatch(
        r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?)?", duration_str.strip(), re.IGNORECASE
    )
    if not match or not any(match.groups()):
        return None
    days, hours, minutes = (int(g) if g else 0 for g in match.groups())
    return days * 24 * 60 + hours * 60 + minutes


def _extract_instructions_summary(instructions_data: Any) -> str | None:
    if isinstance(instructions_data, str):
        return instructions_data[:100] or None
    if isinstance(instructions_data, list):
        for item in instructions_data:
            if isinstance(item, str) and item:
                return item[:100]
            if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"]:
                return item["text"][:100]
    return None


def extract_json_ld_from_html(html: str) -> list[Any]:
    """Extract all JSON-LD blocks from HTML (invalid blocks are skipped)."""
    parser = JsonLdParser()
    try:
        parser.feed(html)
    except Exception:
        return []

    objects = []
    for block in parser.json_ld_blocks:
        try:
            objects.append(json.loads(block))
        except json.JSONDecodeError:
            continue
    return objects


def _is_recipe(item: dict) -> bool:
    item_type = item.get("@type", "")
    types = item_type if isinstance(item_type, list) else [item_type]
    return any(isinstance(t, str) and t.rsplit("/", 1)[-1] == "Recipe" for t in types)


def find_recipes_in_json_ld(json_ld_objects: list) -> list[RecipeCandidate]:
    """Find and parse all Recipe objects (top-level, in lists, or inside @graph)."""
    recipes: list[RecipeCandidate] = []
    stack: list[Any] = list(json_ld_objects)
    while stack:
        obj = stack.pop(0)
        if isinstance(obj, list):
            stack[0:0] = obj
            continue
        if not isinstance(obj, dict):
            continue
        if isinstance(obj.get("@graph"), list):
            stack[0:0] = obj["@graph"]
        if _is_recipe(obj):
            recipe = parse_json_ld_recipe(obj)
            if recipe:
                recipes.append(recipe)
    return recipes


class _OgImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.image: str | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag != "meta" or self.image is not None:
            return
        attrs_dict = dict(attrs)
        if (attrs_dict.get("property") or "").strip().lower() == "og:image":
            content = attrs_dict.get("content")
            if isinstance(content, str) and is_https_url(content.strip()):
                self.image = content.strip()


def extract_og_image(html: str) -> str | None:
    """og:image meta tag (https, <= 500 chars) as image fallback: the JSON-LD often has no image."""
    parser = _OgImageParser()
    try:
        parser.feed(html)
    except Exception:
        return None
    return parser.image
