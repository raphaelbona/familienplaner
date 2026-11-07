import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.family_device import FamilyDevice
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    try:
        payload = decode_access_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungültiges Token") from exc

    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nutzer nicht gefunden")
    return user


def require_parent(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role.value not in ("parent_admin", "parent_member"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nur für Elternteile")
    return user


DEVICE_LAST_USED_INTERVAL = timedelta(minutes=10)
DEVICE_UNAUTHORIZED = "Gerät nicht freigegeben"


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def find_active_device(db: Session, token: str | None) -> FamilyDevice | None:
    """The device for this token if known, not revoked and not expired (touches last_used_at, max every 10 min)."""
    if not token:
        return None
    device = db.scalar(select(FamilyDevice).where(FamilyDevice.token_hash == hash_device_token(token)))
    now = datetime.now(UTC)
    if device is None or device.revoked_at is not None or device.expires_at <= now:
        return None
    if device.last_used_at is None or now - device.last_used_at >= DEVICE_LAST_USED_INTERVAL:
        device.last_used_at = now
        db.commit()
    return device


def device_unauthorized() -> HTTPException:
    """The one 401 every device failure mode returns, so nothing about families or tokens leaks."""
    return HTTPException(status.HTTP_401_UNAUTHORIZED, DEVICE_UNAUTHORIZED)


def require_device(
    db: Annotated[Session, Depends(get_db)],
    x_device_token: Annotated[str | None, Header()] = None,
) -> FamilyDevice:
    """A device a parent has approved for the children's login."""
    device = find_active_device(db, x_device_token)
    if device is None:
        raise device_unauthorized()
    return device
