import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    bearer_scheme,
    device_unauthorized,
    find_active_device,
    get_current_user,
    require_device,
    require_parent,
)
from app.core import pin_lockout
from app.core.person_color import CHILD_ORDER, child_color_indices
from app.core.pin_lockout import reset_user_lock
from app.core.security import hash_secret
from app.db.session import get_db
from app.models.family import Family
from app.models.family_device import FamilyDevice
from app.models.user import User, UserRole
from app.schemas.family import (
    ChildAccountRead,
    ChildCreate,
    ChildProfileRead,
    ChildUpdate,
    DashboardUserRead,
    DashboardUserUpsert,
    FamilyLocationUpdate,
    FamilyRead,
    PinAccountRead,
)

router = APIRouter(prefix="/families", tags=["families"])


@router.get("/me", response_model=FamilyRead)
def get_my_family(current_user: Annotated[User, Depends(get_current_user)]) -> FamilyRead:
    return FamilyRead.model_validate(current_user.family)


@router.patch("/me/location", response_model=FamilyRead)
def update_family_location(
    payload: FamilyLocationUpdate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> Family:
    """Set the family's home location, used by the dashboard's weather widget."""
    family = current_user.family
    family.latitude = payload.latitude
    family.longitude = payload.longitude
    family.location_name = payload.location_name
    db.commit()
    db.refresh(family)
    return family


@router.get("/{family_id}/child-accounts", response_model=list[ChildAccountRead])
def list_child_accounts(
    family_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    x_device_token: Annotated[str | None, Header()] = None,
) -> list[ChildAccountRead]:
    """Children of a family (id, display_name, avatar_key and the family-wide color_index only).

    Needs either a device token of this family (paired device) or a login of this family: parents and the
    wall dashboard see all children, a child login only itself. Every failure mode is the same 401.
    """
    stmt = select(User).where(User.family_id == family_id, User.role == UserRole.CHILD)
    device = find_active_device(db, x_device_token)
    if device is None or device.family_id != family_id:
        try:
            viewer = get_current_user(credentials, db)
        except HTTPException:
            raise device_unauthorized() from None
        if viewer.family_id != family_id:
            raise device_unauthorized()
        if viewer.role == UserRole.CHILD:
            stmt = stmt.where(User.id == viewer.id)
    indices = child_color_indices(db, family_id)
    return [
        ChildAccountRead.model_validate(child).model_copy(update={"color_index": indices[child.id]})
        for child in db.scalars(stmt.order_by(*CHILD_ORDER)).all()
    ]


@router.get("/{family_id}/pin-accounts", response_model=list[PinAccountRead])
def list_pin_accounts(
    family_id: uuid.UUID,
    device: Annotated[FamilyDevice, Depends(require_device)],
    db: Annotated[Session, Depends(get_db)],
) -> list[User]:
    """Accounts that can log in on a paired tablet with a PIN: children and the optional wall-dashboard user,
    never parents. Only for a device a parent paired for this very family (same 401 for every other case)."""
    if device.family_id != family_id:
        raise device_unauthorized()
    accounts = db.scalars(
        select(User)
        .where(User.family_id == family_id, User.role.in_([UserRole.CHILD, UserRole.DASHBOARD]))
        .order_by(User.role.desc(), User.display_name)
    ).all()
    return list(accounts)


def _profile_with_color(db: Session, child: User) -> ChildProfileRead:
    profile = ChildProfileRead.model_validate(child)
    profile.color_index = child_color_indices(db, child.family_id)[child.id]
    return profile


@router.post("/children", response_model=ChildProfileRead, status_code=status.HTTP_201_CREATED)
def create_child(
    payload: ChildCreate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> ChildProfileRead:
    """Create a new child account in the current family."""
    child = User(
        family_id=current_user.family_id,
        role=UserRole.CHILD,
        display_name=payload.display_name,
        birth_year=payload.birth_year,
        avatar_key=payload.avatar_key,
        pin_hash=hash_secret(payload.pin),
    )
    db.add(child)
    db.commit()
    db.refresh(child)
    return _profile_with_color(db, child)


@router.get("/children", response_model=list[ChildProfileRead])
def list_children(
    current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]
) -> list[ChildProfileRead]:
    """List child accounts of the current family.

    Parents and the wall dashboard see every child; a child login only sees its own record
    (never siblings).
    """
    stmt = select(User).where(User.family_id == current_user.family_id, User.role == UserRole.CHILD)
    if current_user.role == UserRole.CHILD:
        stmt = stmt.where(User.id == current_user.id)
    is_parent = current_user.role in (UserRole.PARENT_ADMIN, UserRole.PARENT_MEMBER)
    now = pin_lockout.clock()
    indices = child_color_indices(db, current_user.family_id)
    result = []
    for child in db.scalars(stmt.order_by(*CHILD_ORDER)).all():
        profile = ChildProfileRead.model_validate(child)
        profile.color_index = indices[child.id]
        locked = child.pin_locked_until is not None and child.pin_locked_until > now
        profile.pin_locked_until = child.pin_locked_until if is_parent and locked else None
        result.append(profile)
    return result


@router.get("/dashboard-user", response_model=DashboardUserRead | None)
def get_dashboard_user(
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> User | None:
    return db.scalar(
        select(User).where(User.family_id == current_user.family_id, User.role == UserRole.DASHBOARD)
    )


@router.put("/dashboard-user", response_model=DashboardUserRead)
def upsert_dashboard_user(
    payload: DashboardUserUpsert,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    dashboard_user = db.scalar(
        select(User).where(User.family_id == current_user.family_id, User.role == UserRole.DASHBOARD)
    )

    if dashboard_user is None:
        if payload.pin is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "PIN ist für das Wand-Dashboard erforderlich")
        dashboard_user = User(
            family_id=current_user.family_id,
            role=UserRole.DASHBOARD,
            display_name=payload.display_name,
            avatar_key=payload.avatar_key,
            pin_hash=hash_secret(payload.pin),
        )
        db.add(dashboard_user)
    else:
        dashboard_user.display_name = payload.display_name
        dashboard_user.avatar_key = payload.avatar_key
        if payload.pin is not None:
            dashboard_user.pin_hash = hash_secret(payload.pin)
            reset_user_lock(dashboard_user)

    db.commit()
    db.refresh(dashboard_user)
    return dashboard_user


@router.patch("/children/{child_id}", response_model=ChildProfileRead)
def update_child(
    child_id: uuid.UUID,
    payload: ChildUpdate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> ChildProfileRead:
    """Update a child account (parent-only, tenancy-checked)."""
    child = db.get(User, child_id)
    if child is None or child.family_id != current_user.family_id or child.role != UserRole.CHILD:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kind nicht gefunden")

    update_data = payload.model_dump(exclude_unset=True)
    if "pin" in update_data:
        pin_value = update_data.pop("pin")
        # Only hash and set pin_hash if pin is not None (treat explicit null as "no change")
        if pin_value is not None:
            update_data["pin_hash"] = hash_secret(pin_value)
            reset_user_lock(child)

    for key, value in update_data.items():
        setattr(child, key, value)

    db.commit()
    db.refresh(child)
    return _profile_with_color(db, child)


@router.post("/children/{child_id}/unlock", status_code=status.HTTP_204_NO_CONTENT)
def unlock_child(
    child_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Clear a PIN lockout of a child or of the wall-dashboard user (parent-only, tenancy-checked)."""
    user = db.get(User, child_id)
    if (
        user is None
        or user.family_id != current_user.family_id
        or user.role not in (UserRole.CHILD, UserRole.DASHBOARD)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kind nicht gefunden")
    reset_user_lock(user)
    db.commit()


@router.delete("/children/{child_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_child(
    child_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Delete a child account (parent-only, tenancy-checked)."""
    child = db.get(User, child_id)
    if child is None or child.family_id != current_user.family_id or child.role != UserRole.CHILD:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kind nicht gefunden")

    db.delete(child)
    db.commit()


@router.post("/invite")
def invite_family_member(current_user: Annotated[User, Depends(get_current_user)]) -> dict:
    # TODO Phase 2: echten Einladungslink mit Ablaufzeit generieren und per E-Mail versenden.
    return {"detail": "Einladungs-Flow noch nicht implementiert.", "family_id": str(current_user.family_id)}
