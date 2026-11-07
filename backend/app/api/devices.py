"""Pairing of devices for the children's login: parents approve a device once, only approved devices may
show the child/wall-dashboard picker and do PIN logins (privacy: strangers must not see the children)."""

import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import hash_device_token, require_parent
from app.db.session import get_db
from app.models.family_device import FamilyDevice
from app.models.user import User
from app.schemas.device import DeviceCreate, DevicePaired, DeviceRead

MAX_ACTIVE_DEVICES = 10

router = APIRouter(prefix="/families/devices", tags=["devices"])


@router.post("", response_model=DevicePaired, status_code=status.HTTP_201_CREATED)
def pair_device(
    payload: DeviceCreate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> DevicePaired:
    now = datetime.now(UTC)
    active = db.scalar(
        select(func.count())
        .select_from(FamilyDevice)
        .where(
            FamilyDevice.family_id == current_user.family_id,
            FamilyDevice.revoked_at.is_(None),
            FamilyDevice.expires_at > now,
        )
    )
    if (active or 0) >= MAX_ACTIVE_DEVICES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Maximal {MAX_ACTIVE_DEVICES} freigegebene Geräte. Bitte zuerst eines widerrufen.",
        )

    token = secrets.token_urlsafe(32)
    device = FamilyDevice(
        family_id=current_user.family_id,
        label=payload.label,
        token_hash=hash_device_token(token),
        created_by_user_id=current_user.id,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return DevicePaired(id=device.id, label=device.label, device_token=token, expires_at=device.expires_at)


@router.get("", response_model=list[DeviceRead])
def list_devices(
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> list[DeviceRead]:
    devices = db.scalars(
        select(FamilyDevice)
        .where(FamilyDevice.family_id == current_user.family_id)
        .order_by(FamilyDevice.created_at.desc())
    ).all()
    return [
        DeviceRead(
            id=d.id,
            label=d.label,
            created_at=d.created_at,
            last_used_at=d.last_used_at,
            expires_at=d.expires_at,
            revoked=d.revoked_at is not None,
        )
        for d in devices
    ]


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_device(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    device = db.get(FamilyDevice, device_id)
    if device is None or device.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gerät nicht gefunden")
    if device.revoked_at is None:
        device.revoked_at = datetime.now(UTC)
        db.commit()
