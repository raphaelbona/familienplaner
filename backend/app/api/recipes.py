import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_parent
from app.core.config import get_settings
from app.core.discover_cache import DiscoverCache
from app.core.discover_limits import DiscoverRateLimited, DiscoverRateLimiter, DiscoverSlots
from app.core.recipe_access import get_visible_recipe, recipe_visible_to
from app.core.recipe_discovery import RecipeDiscoveryService
from app.core.recipe_rating import calculate_rating_stats
from app.core.recipe_sources.types import RecipeCandidate
from app.core.recipe_sources.url_policy import THEMEALDB_HOSTS, is_https_url, is_trusted_https_url, parse_domains
from app.db.session import get_db
from app.models.food_preference import RecipeRating
from app.models.meal_plan import MealPlanEntry
from app.models.recipe import Ingredient, Recipe, RecipeIngredient
from app.models.user import User, UserRole
from app.schemas.recipe import (
    IngredientCreate,
    IngredientRead,
    RecipeCreate,
    RecipeRead,
    RecipeSuggestion,
    RecipeSuggestRequest,
)
from app.schemas.recipe_discovery import AdoptRequest, AdoptResponse, DiscoverRequest
from app.schemas.recipe_rating import RecipeRatingCreate, RecipeRatingRead

# Candidates shown by /recipes/discover, adoptable by id: family-scoped, 1h TTL, size-bounded.
discover_cache = DiscoverCache(ttl_seconds=3600, max_per_family=50, max_total=2000)

# /recipes/discover abuse protection (in-process): per-family rate limit + global concurrency cap.
discover_limiter = DiscoverRateLimiter(min_interval=10.0, daily_cap=100)
discover_slots = DiscoverSlots(max_concurrent=4)

# Limits matching the recipe / ingredient columns.
MAX_RECIPE_NAME = 150
MAX_INGREDIENT_NAME = 100
MAX_SOURCE_NAME = 100
MAX_INSTRUCTIONS = 4000
MAX_ADOPTED_INGREDIENTS = 60

router = APIRouter(tags=["recipes"])


@router.post("/ingredients", response_model=IngredientRead, status_code=status.HTTP_201_CREATED)
def create_ingredient(
    payload: IngredientCreate,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Ingredient:
    ingredient = Ingredient(**payload.model_dump())
    db.add(ingredient)
    db.commit()
    db.refresh(ingredient)
    return ingredient


@router.get("/ingredients", response_model=list[IngredientRead])
def list_ingredients(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[Ingredient]:
    return list(db.scalars(select(Ingredient).order_by(Ingredient.name)))


@router.get("/recipes", response_model=list[RecipeRead])
def list_recipes(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[Recipe]:
    """Global (legacy) recipes plus the caller's own family recipes."""
    return list(
        db.scalars(select(Recipe).where(recipe_visible_to(current_user.family_id)).order_by(Recipe.name))
    )


@router.post("/recipes", response_model=RecipeRead, status_code=status.HTTP_201_CREATED)
def create_recipe(
    payload: RecipeCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Recipe:
    recipe = Recipe(
        family_id=current_user.family_id,
        name=payload.name,
        instructions=payload.instructions,
        servings=payload.servings,
        prep_minutes=payload.prep_minutes,
    )
    db.add(recipe)
    db.flush()
    for item in payload.ingredients:
        db.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                ingredient_id=item.ingredient_id,
                quantity=item.quantity,
                unit=item.unit,
            )
        )
    db.commit()
    db.refresh(recipe)
    return recipe


@router.post("/recipes/suggest", response_model=list[RecipeSuggestion])
def suggest_recipes(
    payload: RecipeSuggestRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[RecipeSuggestion]:
    """Rank visible recipes by how many of the given (Wunsch-)Zutaten they use.

    MVP-Matching: einfache Überschneidung Rezept-Zutaten <-> vorhandene Zutaten,
    sortiert nach Anzahl Treffer absteigend, dann nach wenigsten fehlenden Zutaten.
    """
    available = set(payload.available_ingredient_ids)
    recipes = db.scalars(select(Recipe).where(recipe_visible_to(current_user.family_id)))

    suggestions: list[RecipeSuggestion] = []
    for recipe in recipes:
        links = db.scalars(select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id))
        required_ids = {link.ingredient_id for link in links}
        if not required_ids:
            continue
        matched = len(required_ids & available)
        if matched == 0:
            continue
        suggestions.append(
            RecipeSuggestion(
                recipe=RecipeRead.model_validate(recipe),
                matched_ingredients=matched,
                missing_ingredients=len(required_ids - available),
            )
        )

    suggestions.sort(key=lambda s: (-s.matched_ingredients, s.missing_ingredients))
    return suggestions


# ============================================================================
# Ratings
# ============================================================================


@router.post("/recipes/{recipe_id}/ratings", response_model=RecipeRatingRead, status_code=status.HTTP_201_CREATED)
def rate_recipe(
    recipe_id: uuid.UUID,
    payload: RecipeRatingCreate,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RecipeRatingRead:
    """Rate a recipe for a family member.

    A child can rate as themselves. A parent can rate on behalf of any member of their family.
    The shared wall-dashboard account cannot rate. The recipe must be visible to the caller's
    family (global, or owned by it); otherwise 404. At most one rating exists per member, recipe
    and day: a second post the same day updates the stars (200) instead of adding a row (201).
    """
    if current_user.role == UserRole.DASHBOARD:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Dashboard users cannot rate recipes")
    recipe = get_visible_recipe(db, recipe_id, current_user.family_id)
    if recipe is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recipe not found")

    rater_id = current_user.id
    if payload.user_id is not None and payload.user_id != current_user.id:
        if current_user.role not in (UserRole.PARENT_ADMIN, UserRole.PARENT_MEMBER):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only parents can rate on behalf of others")
        rater = db.get(User, payload.user_id)
        if rater is None or rater.family_id != current_user.family_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
        if rater.role == UserRole.DASHBOARD:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Dashboard account cannot be rated for")
        rater_id = rater.id

    rating = _find_rating(db, recipe.id, rater_id, payload.cooked_on)
    if rating is None:
        try:
            with db.begin_nested():  # savepoint: losing an insert race must not poison the transaction
                rating = RecipeRating(
                    family_id=current_user.family_id,
                    recipe_id=recipe.id,
                    user_id=rater_id,
                    stars=payload.stars,
                    cooked_on=payload.cooked_on,
                )
                db.add(rating)
                db.flush()
        except IntegrityError:
            rating = _find_rating(db, recipe.id, rater_id, payload.cooked_on)
            if rating is None:
                raise
            rating.stars = payload.stars
            response.status_code = status.HTTP_200_OK
    else:
        rating.stars = payload.stars
        response.status_code = status.HTTP_200_OK
    db.commit()
    db.refresh(rating)

    return RecipeRatingRead(id=str(rating.id), stars=rating.stars, cooked_on=rating.cooked_on)


def _find_rating(db: Session, recipe_id: uuid.UUID, user_id: uuid.UUID, cooked_on: date) -> RecipeRating | None:
    return db.scalars(
        select(RecipeRating).where(
            RecipeRating.recipe_id == recipe_id,
            RecipeRating.user_id == user_id,
            RecipeRating.cooked_on == cooked_on,
        )
    ).first()


@router.get("/recipes/{recipe_id}/ratings-summary")
def get_recipe_rating_summary(
    recipe_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Rating summary of a visible recipe, counting only this family's ratings."""
    recipe = get_visible_recipe(db, recipe_id, current_user.family_id)
    if recipe is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recipe not found")

    stars = list(
        db.scalars(
            select(RecipeRating.stars).where(
                RecipeRating.recipe_id == recipe.id, RecipeRating.family_id == current_user.family_id
            )
        )
    )
    stats = calculate_rating_stats(stars)
    return {
        "average": stats["average"],
        "rating_count": stats["rating_count"],
        "is_proven": stats["is_proven"],
        "is_rejected": stats["is_rejected"],
    }


# ============================================================================
# Discovery
# ============================================================================


@router.post("/recipes/discover")
def discover_recipes(
    payload: DiscoverRequest,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Discover recipes for the selected eaters (parent only)."""
    eater_ids = list(dict.fromkeys(payload.eater_user_ids))
    for uid in eater_ids:
        eater = db.get(User, uid)
        if eater is None or eater.family_id != current_user.family_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Eater not found")
        if eater.role == UserRole.DASHBOARD:  # the shared wall account is not a person
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Dashboard account cannot be an eater")

    if not discover_slots.try_acquire():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Recipe search is busy, try again shortly",
            headers={"Retry-After": "5"},
        )
    try:
        try:
            discover_limiter.check(current_user.family_id)
        except DiscoverRateLimited as exc:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, exc.reason, headers={"Retry-After": str(exc.retry_after)}
            ) from exc
        result, candidates = RecipeDiscoveryService().discover_with_candidates(
            db,
            current_user.family_id,
            eater_ids,
            payload.meal_type,
            payload.extra_ingredients,
        )
    finally:
        discover_slots.release()

    entries = []
    for item, candidate in zip(result["discover"], candidates, strict=True):
        candidate_id = str(uuid.uuid4())
        item["candidate_id"] = candidate_id
        entries.append((candidate_id, candidate))
    discover_cache.put_many(current_user.family_id, entries)
    return result


def _bounded(value: int | None, upper: int, default: int) -> int:
    return value if value and 0 < value <= upper else default


def _clean_name(value: str | None, max_len: int) -> str:
    return " ".join((value or "").split())[:max_len]


def _allowed_source_domains() -> list[str]:
    return [*parse_domains(get_settings().recipe_search_domains), *THEMEALDB_HOSTS]


def _find_family_recipe(db: Session, family_id: uuid.UUID, source_url: str) -> Recipe | None:
    return db.scalar(select(Recipe).where(Recipe.family_id == family_id, Recipe.source_url == source_url))


def _find_or_create_ingredient(db: Session, name: str) -> Ingredient:
    """Reuse a global ingredient by case-insensitive name, else create it."""
    existing = db.scalars(select(Ingredient).where(func.lower(Ingredient.name) == name.lower()).limit(1)).first()
    if existing is not None:
        return existing
    try:
        with db.begin_nested():  # savepoint: a lost race only rolls back this insert, not the adoption
            ingredient = Ingredient(name=name)
            db.add(ingredient)
            db.flush()
        return ingredient
    except IntegrityError:
        existing = db.scalars(select(Ingredient).where(func.lower(Ingredient.name) == name.lower()).limit(1)).first()
        if existing is None:
            raise
        return existing


@router.post("/recipes/discover/adopt", response_model=AdoptResponse, status_code=status.HTTP_201_CREATED)
def adopt_recipe(
    payload: AdoptRequest,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> AdoptResponse:
    """Adopt a discovered candidate into the family's recipes, optionally planning it."""
    candidate: RecipeCandidate | None = discover_cache.get(current_user.family_id, str(payload.candidate_id))
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found or expired")

    if not is_trusted_https_url(candidate.source_url, _allowed_source_domains()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Candidate source is not allowed")

    name = _clean_name(candidate.title, MAX_RECIPE_NAME)
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Candidate has no title")

    recipe = _find_family_recipe(db, current_user.family_id, candidate.source_url)
    created = recipe is None
    if recipe is None:
        try:
            with db.begin_nested():  # savepoint: losing the unique-index race must not poison the transaction
                recipe = Recipe(
                    family_id=current_user.family_id,
                    name=name,
                    instructions=(candidate.instructions_summary or "")[:MAX_INSTRUCTIONS],
                    servings=_bounded(candidate.servings, 100, 4),
                    prep_minutes=_bounded(candidate.prep_minutes, 10000, 30),
                    source_url=candidate.source_url,
                    source_name=_clean_name(candidate.source_name, MAX_SOURCE_NAME) or None,
                    image_url=candidate.image_url if is_https_url(candidate.image_url) else None,
                )
                db.add(recipe)
                db.flush()

                seen: set[str] = set()
                for raw_name in candidate.ingredients[:MAX_ADOPTED_INGREDIENTS]:
                    ing_name = _clean_name(raw_name, MAX_INGREDIENT_NAME)
                    if not ing_name or ing_name.lower() in seen:
                        continue
                    seen.add(ing_name.lower())
                    ingredient = _find_or_create_ingredient(db, ing_name)
                    db.add(RecipeIngredient(recipe_id=recipe.id, ingredient_id=ingredient.id))
                db.flush()
        except IntegrityError:
            recipe = _find_family_recipe(db, current_user.family_id, candidate.source_url)
            if recipe is None:
                raise
            created = False  # a concurrent adopt of the same source_url won: reuse its recipe

    entry_id = None
    if payload.plan_date is not None and payload.meal_type is not None:
        entry = MealPlanEntry(
            family_id=current_user.family_id,
            date=payload.plan_date,
            meal_type=payload.meal_type,
            recipe_id=recipe.id,
        )
        db.add(entry)
        db.flush()
        entry_id = entry.id

    db.commit()
    return AdoptResponse(recipe_id=recipe.id, created=created, meal_plan_entry_id=entry_id)
