import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import device_unauthorized, require_device
from app.core.config import get_settings
from app.core.email import send_password_reset_email
from app.core.pin_lockout import pin_login_user
from app.core.security import create_access_token, hash_secret, verify_secret
from app.db.session import get_db
from app.models.family import Family
from app.models.family_device import FamilyDevice
from app.models.password_reset import PasswordResetToken
from app.models.user import User, UserRole
from app.schemas.auth import (
    ChildPinLoginRequest,
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    PinLoginRequest,
    RegisterFamilyRequest,
    TokenResponse,
)

RESET_TOKEN_TTL_MINUTES = 30
_GENERIC_RESET_RESPONSE = {
    "detail": "Falls diese E-Mail registriert ist, wurde ein Link zum Zurücksetzen des Passworts verschickt."
}

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register-family", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register_family(payload: RegisterFamilyRequest, db: Annotated[Session, Depends(get_db)]) -> TokenResponse:
    if db.scalar(select(User).where(User.email == payload.admin_email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "E-Mail bereits registriert")

    family = Family(name=payload.family_name)
    db.add(family)
    db.flush()

    admin = User(
        family_id=family.id,
        role=UserRole.PARENT_ADMIN,
        display_name=payload.admin_display_name,
        email=payload.admin_email,
        password_hash=hash_secret(payload.admin_password),
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    token = create_access_token(admin.id, family.id, admin.role.value)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Annotated[Session, Depends(get_db)]) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not user.password_hash or not verify_secret(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-Mail oder Passwort falsch")

    token = create_access_token(user.id, user.family_id, user.role.value)
    return TokenResponse(access_token=token)


@router.post("/password-reset/request")
def request_password_reset(payload: PasswordResetRequest, db: Annotated[Session, Depends(get_db)]) -> dict:
    """Always returns the same generic response, whether or not the email is registered."""
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not user.password_hash:
        return _GENERIC_RESET_RESPONSE

    raw_token = secrets.token_urlsafe(32)
    reset_token = PasswordResetToken(
        user_id=user.id,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
    )
    db.add(reset_token)
    db.commit()

    settings = get_settings()
    reset_link = f"{settings.app_base_url}/passwort-zuruecksetzen?token={raw_token}"
    send_password_reset_email(user.email, reset_link, settings)

    return _GENERIC_RESET_RESPONSE


@router.post("/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirm, db: Annotated[Session, Depends(get_db)]) -> dict:
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    reset_token = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash))

    if reset_token is None or reset_token.used_at is not None or reset_token.expires_at < datetime.now(UTC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Link ist ungültig oder abgelaufen.")

    user = db.get(User, reset_token.user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Link ist ungültig oder abgelaufen.")

    user.password_hash = hash_secret(payload.new_password)
    reset_token.used_at = datetime.now(UTC)
    db.commit()

    return {"detail": "Passwort wurde erfolgreich zurückgesetzt."}


def _pin_login(payload: ChildPinLoginRequest, db: Session, device: FamilyDevice) -> TokenResponse:
    # Only a device this family's parent paired may do PIN logins (same 401 whatever is wrong with the device).
    if device.family_id != payload.family_id:
        raise device_unauthorized()
    user = pin_login_user(db, device, payload.family_id, payload.user_id, payload.pin)

    token = create_access_token(user.id, user.family_id, user.role.value)
    return TokenResponse(access_token=token)


@router.post("/pin", response_model=TokenResponse)
def pin_login(
    payload: PinLoginRequest,
    db: Annotated[Session, Depends(get_db)],
    device: Annotated[FamilyDevice, Depends(require_device)],
) -> TokenResponse:
    return _pin_login(payload, db, device)


@router.post("/child-pin", response_model=TokenResponse)
def child_pin_login(
    payload: ChildPinLoginRequest,
    db: Annotated[Session, Depends(get_db)],
    device: Annotated[FamilyDevice, Depends(require_device)],
) -> TokenResponse:
    return _pin_login(payload, db, device)
