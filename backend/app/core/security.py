from datetime import UTC, datetime, timedelta
from uuid import UUID

import bcrypt
from jose import JWTError, jwt

from app.core.config import get_settings


def hash_secret(raw: str) -> str:
    """Hash a parent password or a child's 4-digit PIN with the same scheme."""
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_secret(raw: str, hashed: str) -> bool:
    return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: UUID, family_id: UUID, role: str) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "family_id": str(family_id),
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except JWTError as exc:
        raise ValueError("invalid or expired token") from exc


def create_oauth_state_token(user_id: UUID) -> str:
    """Create a short-lived OAuth state token (10 minute lifetime).

    This token is used to maintain state during the OAuth2 flow and prevent CSRF attacks.
    It carries the user_id so the callback endpoint can identify which user initiated the flow.

    Args:
        user_id: The UUID of the user initiating OAuth.

    Returns:
        A JWT token string with a 10-minute expiration.
    """
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=10)
    payload = {
        "sub": str(user_id),
        "purpose": "google_oauth_state",
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_oauth_state_token(token: str) -> UUID:
    """Decode and validate an OAuth state token.

    Verifies the token is valid, not expired, and has the correct purpose claim.

    Args:
        token: The JWT token string to decode.

    Returns:
        The user_id (as UUID) from the token's 'sub' claim.

    Raises:
        ValueError: If token is invalid, expired, or has wrong purpose.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except JWTError as exc:
        raise ValueError("invalid or expired token") from exc

    if payload.get("purpose") != "google_oauth_state":
        raise ValueError("invalid token purpose")

    return UUID(payload["sub"])
