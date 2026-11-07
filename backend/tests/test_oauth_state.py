import uuid

import pytest

from app.core.security import create_oauth_state_token, decode_oauth_state_token


def test_create_and_decode_oauth_state_token():
    user_id = uuid.uuid4()
    token = create_oauth_state_token(user_id)
    assert isinstance(token, str)
    assert len(token) > 0

    decoded_user_id = decode_oauth_state_token(token)
    assert decoded_user_id == user_id


def test_decode_oauth_state_token_with_wrong_purpose_raises_error():
    from datetime import UTC, datetime, timedelta

    from jose import jwt

    from app.core.config import get_settings

    user_id = uuid.uuid4()

    # Manually create a token with wrong purpose to test validation
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=10)
    payload = {
        "sub": str(user_id),
        "purpose": "wrong_purpose",
        "exp": expire,
    }
    wrong_token = jwt.encode(payload, settings.secret_key, algorithm="HS256")

    with pytest.raises(ValueError):
        decode_oauth_state_token(wrong_token)


def test_decode_expired_oauth_state_token_raises_error():
    from datetime import UTC, datetime, timedelta

    from jose import jwt

    from app.core.config import get_settings

    user_id = uuid.uuid4()

    # Create an expired token
    settings = get_settings()
    # Set expiration in the past
    expire = datetime.now(UTC) - timedelta(minutes=1)
    payload = {
        "sub": str(user_id),
        "purpose": "google_oauth_state",
        "exp": expire,
    }
    expired_token = jwt.encode(payload, settings.secret_key, algorithm="HS256")

    with pytest.raises(ValueError):
        decode_oauth_state_token(expired_token)


def test_decode_invalid_token_raises_error():
    with pytest.raises(ValueError):
        decode_oauth_state_token("invalid-token")
