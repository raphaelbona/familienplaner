"""Slice 2: Food preferences API endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_parent
from app.db.session import get_db
from app.models.food_preference import FoodPreferenceKind, UserFoodPreference
from app.models.user import User, UserRole
from app.schemas.food_preference import MemberFoodPreferences, UserFoodPreferencesRead, UserFoodPreferencesWrite

router = APIRouter(tags=["food-preferences"], prefix="/food-preferences")


def _load_target(db: Session, current_user: User, user_id: uuid.UUID) -> User:
    """Resolve the member whose preferences are accessed, enforcing tenancy and role rules.

    * a member of another family is indistinguishable from a missing one (404);
    * the shared wall-dashboard account has no access at all;
    * a child may only access its own preferences; parents may access every family member.
    """
    target = db.get(User, user_id)
    if target is None or target.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    if current_user.role == UserRole.DASHBOARD:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Dashboard users cannot access preferences")
    if current_user.role == UserRole.CHILD and current_user.id != target.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot access other member's preferences")
    return target


def _split_by_kind(prefs) -> UserFoodPreferencesRead:
    result = UserFoodPreferencesRead()
    for pref in prefs:
        if pref.kind == FoodPreferenceKind.LIKES:
            result.likes.append(pref.ingredient_name)
        elif pref.kind == FoodPreferenceKind.DISLIKES:
            result.dislikes.append(pref.ingredient_name)
        elif pref.kind == FoodPreferenceKind.ALLERGY:
            result.allergies.append(pref.ingredient_name)
    return result


def _dedupe(items: list[str]) -> list[str]:
    """Drop blanks and case-insensitive duplicates (first spelling wins, order kept)."""
    seen: set[str] = set()
    result = []
    for item in items:
        item = item.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            result.append(item)
    return result


@router.get("/members/{user_id}", response_model=UserFoodPreferencesRead)
def get_member_preferences(
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserFoodPreferencesRead:
    """Get food preferences for a family member (child: own only; parent: any member)."""
    target = _load_target(db, current_user, user_id)
    prefs = db.scalars(
        select(UserFoodPreference)
        .where(UserFoodPreference.user_id == target.id, UserFoodPreference.family_id == current_user.family_id)
        .order_by(UserFoodPreference.ingredient_name)
    )
    return _split_by_kind(prefs)


def _replace_preferences(
    db: Session,
    target: User,
    family_id: uuid.UUID,
    likes: list[str],
    dislikes: list[str],
    allergies: list[str],
) -> None:
    # Serialise concurrent replaces of the same member: the second one waits for the first to commit.
    db.execute(select(User.id).where(User.id == target.id).with_for_update())
    db.execute(delete(UserFoodPreference).where(UserFoodPreference.user_id == target.id))
    for kind, items in (
        (FoodPreferenceKind.LIKES, likes),
        (FoodPreferenceKind.DISLIKES, dislikes),
        (FoodPreferenceKind.ALLERGY, allergies),
    ):
        for ingredient in items:
            db.add(UserFoodPreference(family_id=family_id, user_id=target.id, ingredient_name=ingredient, kind=kind))
    db.flush()


@router.put("/members/{user_id}", response_model=UserFoodPreferencesRead)
def update_member_preferences(
    user_id: uuid.UUID,
    payload: UserFoodPreferencesWrite,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserFoodPreferencesRead:
    """Replace the whole preference set of a member (size limits are enforced by the schema)."""
    target = _load_target(db, current_user, user_id)

    likes = _dedupe(payload.likes)
    dislikes = _dedupe(payload.dislikes)
    allergies = _dedupe(payload.allergies)

    for attempt in range(2):  # a concurrent replace may still trip the unique constraint: retry once
        try:
            with db.begin_nested():  # savepoint: a failed attempt must not poison the transaction
                _replace_preferences(db, target, current_user.family_id, likes, dislikes, allergies)
            break
        except IntegrityError as exc:
            if attempt == 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Preferences were changed concurrently, please retry"
                ) from exc
    db.commit()

    return UserFoodPreferencesRead(likes=likes, dislikes=dislikes, allergies=allergies)


@router.get("/", response_model=list[MemberFoodPreferences])
def get_all_family_preferences(
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> list[MemberFoodPreferences]:
    """Preferences of all family members (parent only); the shared wall-dashboard account is not a member."""
    rows = db.execute(
        select(User, UserFoodPreference)
        .outerjoin(
            UserFoodPreference,
            (UserFoodPreference.user_id == User.id) & (UserFoodPreference.family_id == User.family_id),
        )
        .where(User.family_id == current_user.family_id, User.role != UserRole.DASHBOARD)
        .order_by(User.display_name, User.id, UserFoodPreference.ingredient_name)
    )
    members: dict[uuid.UUID, MemberFoodPreferences] = {}
    for member, pref in rows:
        entry = members.setdefault(
            member.id, MemberFoodPreferences(user_id=member.id, display_name=member.display_name)
        )
        if pref is not None:
            split = _split_by_kind([pref])
            entry.likes += split.likes
            entry.dislikes += split.dislikes
            entry.allergies += split.allergies
    return list(members.values())
