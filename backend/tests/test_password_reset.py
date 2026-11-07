"""Tests for the parent password-reset flow."""

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import select

from app.models.password_reset import PasswordResetToken
from app.models.user import User


def _register_parent(client, email="eltern@example.ch", password="s3hr-sicher"):
    response = client.post(
        "/auth/register-family",
        json={
            "family_name": "Muster",
            "admin_display_name": "Papa",
            "admin_email": email,
            "admin_password": password,
        },
    )
    return response.json()["access_token"]


def test_request_reset_for_unknown_email_returns_generic_response(client, db_session):
    """Requesting a reset for an email that doesn't exist must not reveal that fact."""
    response = client.post("/auth/password-reset/request", json={"email": "nobody@example.ch"})
    assert response.status_code == 200
    assert "Falls diese E-Mail registriert ist" in response.json()["detail"]

    assert db_session.scalar(select(PasswordResetToken)) is None


def test_request_reset_for_known_email_creates_token_and_logs_link(client, db_session):
    _register_parent(client, email="reset1@example.ch")

    with patch("app.api.auth.send_password_reset_email") as mock_send:
        response = client.post("/auth/password-reset/request", json={"email": "reset1@example.ch"})

    assert response.status_code == 200
    assert "Falls diese E-Mail registriert ist" in response.json()["detail"]
    mock_send.assert_called_once()

    token = db_session.scalar(select(PasswordResetToken))
    assert token is not None
    assert token.used_at is None
    assert token.expires_at > datetime.now(UTC)


def test_confirm_reset_with_valid_token_changes_password(client, db_session):
    _register_parent(client, email="reset2@example.ch", password="alte-password")

    captured_link = {}

    def _capture(to_email, reset_link, settings):
        captured_link["link"] = reset_link

    with patch("app.api.auth.send_password_reset_email", side_effect=_capture):
        client.post("/auth/password-reset/request", json={"email": "reset2@example.ch"})

    raw_token = captured_link["link"].split("token=")[1]

    confirm = client.post(
        "/auth/password-reset/confirm",
        json={"token": raw_token, "new_password": "neue-sichere-pw"},
    )
    assert confirm.status_code == 200

    # Old password no longer works, new one does
    old_login = client.post("/auth/login", json={"email": "reset2@example.ch", "password": "alte-password"})
    assert old_login.status_code == 401

    new_login = client.post("/auth/login", json={"email": "reset2@example.ch", "password": "neue-sichere-pw"})
    assert new_login.status_code == 200


def test_confirm_reset_token_is_single_use(client, db_session):
    _register_parent(client, email="reset3@example.ch", password="alte-password")

    captured_link = {}

    def _capture(to_email, reset_link, settings):
        captured_link["link"] = reset_link

    with patch("app.api.auth.send_password_reset_email", side_effect=_capture):
        client.post("/auth/password-reset/request", json={"email": "reset3@example.ch"})

    raw_token = captured_link["link"].split("token=")[1]

    first = client.post(
        "/auth/password-reset/confirm", json={"token": raw_token, "new_password": "erste-neue-pw"}
    )
    assert first.status_code == 200

    second = client.post(
        "/auth/password-reset/confirm", json={"token": raw_token, "new_password": "zweite-neue-pw"}
    )
    assert second.status_code == 400
    assert "ungültig oder abgelaufen" in second.json()["detail"]


def test_confirm_reset_with_expired_token_rejected(client, db_session):
    _register_parent(client, email="reset4@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "reset4@example.ch"))

    raw_token = "expired-raw-token-value"
    expired = PasswordResetToken(
        user_id=admin.id,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(expired)
    db_session.commit()

    response = client.post(
        "/auth/password-reset/confirm", json={"token": raw_token, "new_password": "irgendeine-pw"}
    )
    assert response.status_code == 400
    assert "ungültig oder abgelaufen" in response.json()["detail"]


def test_confirm_reset_with_bogus_token_rejected(client, db_session):
    response = client.post(
        "/auth/password-reset/confirm", json={"token": "does-not-exist", "new_password": "irgendeine-pw"}
    )
    assert response.status_code == 400
    assert "ungültig oder abgelaufen" in response.json()["detail"]


def test_confirm_reset_rejects_short_password(client, db_session):
    _register_parent(client, email="reset5@example.ch")

    captured_link = {}

    def _capture(to_email, reset_link, settings):
        captured_link["link"] = reset_link

    with patch("app.api.auth.send_password_reset_email", side_effect=_capture):
        client.post("/auth/password-reset/request", json={"email": "reset5@example.ch"})

    raw_token = captured_link["link"].split("token=")[1]

    response = client.post("/auth/password-reset/confirm", json={"token": raw_token, "new_password": "short"})
    assert response.status_code == 422
