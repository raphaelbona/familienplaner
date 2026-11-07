import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.security import create_oauth_state_token
from app.models.calendar import CalendarEvent, CalendarSyncAccount
from app.models.user import User, UserRole
from app.worker.sync_worker import sync_google_calendars


def _register_parent(client, email="eltern@example.ch"):
    response = client.post(
        "/auth/register-family",
        json={
            "family_name": "Muster",
            "admin_display_name": "Papa",
            "admin_email": email,
            "admin_password": "s3hr-sicher",
        },
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_google_connect_returns_503_when_settings_missing(client):
    """Test that google_connect returns 503 when Google settings are not configured."""
    headers = _register_parent(client, email="test@example.ch")

    with patch("app.api.calendar.get_settings") as mock_settings:
        mock_settings.return_value.google_client_id = None
        mock_settings.return_value.google_client_secret = "secret"
        mock_settings.return_value.google_redirect_uri = "http://localhost/callback"

        response = client.post("/calendar/sync/google/connect", headers=headers)
        assert response.status_code == 503
        assert "nicht konfiguriert" in response.json()["detail"]


def test_google_connect_returns_authorization_url(client):
    """Test that google_connect returns an authorization URL when settings are provided."""
    headers = _register_parent(client, email="auth@example.ch")

    with patch("app.api.calendar.get_settings") as mock_settings:
        mock_settings.return_value.google_client_id = "test-client-id"
        mock_settings.return_value.google_client_secret = "test-secret"
        mock_settings.return_value.google_redirect_uri = "http://localhost/callback"
        mock_settings.return_value.secret_key = "test-secret-key"

        response = client.post("/calendar/sync/google/connect", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert "authorization_url" in data
        assert "https://accounts.google.com/o/oauth2/auth" in data["authorization_url"]
        assert "state=" in data["authorization_url"]


def test_google_connect_requires_authentication(client):
    """Test that google_connect requires a valid Bearer token."""
    response = client.post("/calendar/sync/google/connect")
    assert response.status_code == 401


def test_google_callback_with_invalid_state_redirects_with_error(client):
    """Test that google_callback redirects back into the app with an error indicator on invalid state."""
    response = client.get(
        "/calendar/sync/google/callback",
        params={"code": "test-code", "state": "invalid-state"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "/kalender?google=error" in response.headers["location"]


def test_google_callback_without_matching_cookie_redirects_with_error(client):
    """Test that google_callback redirects with an error indicator when state cookie is missing or mismatched."""
    state = create_oauth_state_token(uuid.uuid4())

    # Call without cookie
    response = client.get(
        "/calendar/sync/google/callback", params={"code": "test-code", "state": state}, follow_redirects=False
    )
    assert response.status_code in (302, 307)
    assert "/kalender?google=error" in response.headers["location"]

    # Call with mismatched cookie
    response = client.get(
        "/calendar/sync/google/callback",
        params={"code": "test-code", "state": state},
        cookies={"google_oauth_state": "wrong-cookie-value"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "/kalender?google=error" in response.headers["location"]


def test_google_callback_creates_calendar_sync_account(client, db_session):
    """Test that google_callback creates a CalendarSyncAccount with encrypted token."""
    _register_parent(client, email="callback@example.ch")
    user = db_session.scalar(select(User).where(User.email == "callback@example.ch"))

    with patch("app.api.calendar.get_settings") as mock_settings:
        mock_settings.return_value.google_client_id = "test-client-id"
        mock_settings.return_value.google_client_secret = "test-secret"
        mock_settings.return_value.google_redirect_uri = "http://localhost/callback"
        mock_settings.return_value.secret_key = "test-secret-key"

        with patch("app.api.calendar.Flow") as mock_flow_class:
            # Create mock flow instance
            mock_flow = MagicMock()
            mock_flow_class.from_client_config.return_value = mock_flow

            # Mock credentials
            mock_creds = MagicMock()
            mock_creds.token = "test-access-token"
            mock_creds.refresh_token = "test-refresh-token"
            mock_creds.token_uri = "https://oauth2.googleapis.com/token"
            mock_creds.client_id = "test-client-id"
            mock_creds.client_secret = "test-secret"
            mock_creds.scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
            mock_flow.credentials = mock_creds

            # Create a valid OAuth state token for the user
            state = create_oauth_state_token(user.id)

            # Call callback with the matching cookie
            response = client.get(
                "/calendar/sync/google/callback",
                params={"code": "test-code", "state": state},
                cookies={"google_oauth_state": state},
                follow_redirects=False,
            )

            assert response.status_code in (302, 307)
            assert "/kalender?google=connected" in response.headers["location"]

            # Verify CalendarSyncAccount was created/updated
            account = db_session.scalar(
                select(CalendarSyncAccount).where(
                    (CalendarSyncAccount.user_id == user.id) & (CalendarSyncAccount.provider == "google")
                )
            )
            assert account is not None
            assert account.oauth_token_encrypted is not None

            # Verify token is actually encrypted (not plaintext)
            assert "test-access-token" not in account.oauth_token_encrypted

            # Verify we can decrypt the token
            decrypted = decrypt_secret(account.oauth_token_encrypted)
            creds_dict = json.loads(decrypted)
            assert creds_dict["token"] == "test-access-token"
            assert creds_dict["refresh_token"] == "test-refresh-token"


def test_google_callback_with_invalid_code_returns_400(client, db_session):
    """Test that google_callback returns 400 when authorization code is invalid or replayed."""
    client.post(
        "/auth/register-family",
        json={
            "family_name": "Muster",
            "admin_display_name": "Papa",
            "admin_email": "invalid-code@example.ch",
            "admin_password": "s3hr-sicher",
        },
    )
    user = db_session.scalar(select(User).where(User.email == "invalid-code@example.ch"))

    with patch("app.api.calendar.get_settings") as mock_settings:
        mock_settings.return_value.google_client_id = "test-client-id"
        mock_settings.return_value.google_client_secret = "test-secret"
        mock_settings.return_value.google_redirect_uri = "http://localhost/callback"
        mock_settings.return_value.secret_key = "test-secret-key"

        with patch("app.api.calendar.Flow") as mock_flow_class:
            # Create mock flow instance that raises an OAuth error
            mock_flow = MagicMock()
            mock_flow_class.from_client_config.return_value = mock_flow

            # Simulate OAuth error on invalid/replayed code
            from oauthlib.oauth2.rfc6749.errors import OAuth2Error

            mock_flow.fetch_token.side_effect = OAuth2Error("Invalid authorization code")

            state = create_oauth_state_token(user.id)

            response = client.get(
                "/calendar/sync/google/callback",
                params={"code": "invalid-code", "state": state},
                cookies={"google_oauth_state": state},
                follow_redirects=False,
            )

            assert response.status_code in (302, 307)
            assert "/kalender?google=error" in response.headers["location"]

            # Verify no account was created
            account = db_session.scalar(
                select(CalendarSyncAccount).where(
                    (CalendarSyncAccount.user_id == user.id) & (CalendarSyncAccount.provider == "google")
                )
            )
            assert account is None


def test_google_callback_upserts_existing_account(client, db_session):
    """Test that google_callback updates an existing CalendarSyncAccount."""
    client.post(
        "/auth/register-family",
        json={
            "family_name": "Muster",
            "admin_display_name": "Papa",
            "admin_email": "upsert@example.ch",
            "admin_password": "s3hr-sicher",
        },
    )
    user = db_session.scalar(select(User).where(User.email == "upsert@example.ch"))

    # Create an existing account
    existing_account = CalendarSyncAccount(
        user_id=user.id,
        provider="google",
        oauth_token_encrypted=encrypt_secret(json.dumps({"token": "old-token"})),
    )
    db_session.add(existing_account)
    db_session.commit()

    with patch("app.api.calendar.get_settings") as mock_settings:
        mock_settings.return_value.google_client_id = "test-client-id"
        mock_settings.return_value.google_client_secret = "test-secret"
        mock_settings.return_value.google_redirect_uri = "http://localhost/callback"
        mock_settings.return_value.secret_key = "test-secret-key"

        with patch("app.api.calendar.Flow") as mock_flow_class:
            mock_flow = MagicMock()
            mock_flow_class.from_client_config.return_value = mock_flow

            mock_creds = MagicMock()
            mock_creds.token = "new-access-token"
            mock_creds.refresh_token = "new-refresh-token"
            mock_creds.token_uri = "https://oauth2.googleapis.com/token"
            mock_creds.client_id = "test-client-id"
            mock_creds.client_secret = "test-secret"
            mock_creds.scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
            mock_flow.credentials = mock_creds

            state = create_oauth_state_token(user.id)

            response = client.get(
                "/calendar/sync/google/callback",
                params={"code": "test-code", "state": state},
                cookies={"google_oauth_state": state},
                follow_redirects=False,
            )

            assert response.status_code in (302, 307)
            assert "/kalender?google=connected" in response.headers["location"]

            # Verify the account was updated (not duplicated)
            accounts = db_session.scalars(
                select(CalendarSyncAccount).where(
                    (CalendarSyncAccount.user_id == user.id) & (CalendarSyncAccount.provider == "google")
                )
            ).all()
            assert len(accounts) == 1

            # Verify token was updated
            decrypted = decrypt_secret(accounts[0].oauth_token_encrypted)
            creds_dict = json.loads(decrypted)
            assert creds_dict["token"] == "new-access-token"


def test_sync_google_calendars_creates_events(db_session):
    """Test that sync_google_calendars creates CalendarEvent rows from Google API response."""
    # Create a family, user, and calendar sync account
    from app.models.family import Family

    family = Family(name="TestFamily")
    db_session.add(family)
    db_session.commit()

    user = User(
        family_id=family.id,
        role=UserRole.PARENT_ADMIN,
        display_name="Parent",
        email="sync@example.ch",
    )
    db_session.add(user)
    db_session.commit()

    creds_dict = {
        "token": "test-token",
        "refresh_token": "test-refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "test-id",
        "client_secret": "test-secret",
        "scopes": ["https://www.googleapis.com/auth/calendar.readonly"],
    }
    account = CalendarSyncAccount(
        user_id=user.id,
        provider="google",
        oauth_token_encrypted=encrypt_secret(json.dumps(creds_dict)),
    )
    db_session.add(account)
    db_session.commit()

    # Mock Google API responses
    with patch("app.worker.sync_worker.SessionLocal") as mock_session_local:
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=db_session)
        mock_session.__exit__ = MagicMock(return_value=None)
        mock_session_local.return_value = db_session
        db_session.close = MagicMock()  # Don't actually close the test session

        with patch("app.worker.sync_worker._build_and_refresh_credentials") as mock_build_creds:
            mock_creds = MagicMock()
            mock_build_creds.return_value = mock_creds

            with patch("app.worker.sync_worker.discovery.build") as mock_build:
                mock_service = MagicMock()
                mock_build.return_value = mock_service

                # Mock the events list response
                now = datetime.now(UTC)
                mock_service.events.return_value.list.return_value.execute.return_value = {
                    "items": [
                        {
                            "id": "event1",
                            "summary": "Meeting 1",
                            "start": {"dateTime": (now + timedelta(hours=1)).isoformat()},
                            "end": {"dateTime": (now + timedelta(hours=2)).isoformat()},
                        },
                        {
                            "id": "event2",
                            "summary": "All Day Event",
                            "start": {"date": (now.date()).isoformat()},
                            "end": {"date": (now.date() + timedelta(days=1)).isoformat()},
                        },
                        {
                            "id": "event3",
                            # Missing summary should default to "(Ohne Titel)"
                            "start": {"dateTime": (now + timedelta(days=1)).isoformat()},
                            "end": {"dateTime": (now + timedelta(days=1, hours=1)).isoformat()},
                        },
                    ]
                }

                family_id = family.id
                account_id = account.id
                sync_google_calendars()

                # Verify events were created
                events = db_session.scalars(
                    select(CalendarEvent).where(CalendarEvent.family_id == family_id)
                ).all()
                assert len(events) == 3

                # Verify event details
                event1 = next((e for e in events if e.external_id == "event1"), None)
                assert event1 is not None
                assert event1.title == "Meeting 1"
                assert event1.source == "google"
                assert event1.all_day is False

                event2 = next((e for e in events if e.external_id == "event2"), None)
                assert event2 is not None
                assert event2.title == "All Day Event"
                assert event2.all_day is True

                event3 = next((e for e in events if e.external_id == "event3"), None)
                assert event3 is not None
                assert event3.title == "(Ohne Titel)"

                # Verify last_synced_at was updated
                updated_account = db_session.get(CalendarSyncAccount, account_id)
                assert updated_account is not None
                assert updated_account.last_synced_at is not None


def test_sync_google_calendars_upserts_events(db_session):
    """Test that sync_google_calendars upserts events (no duplicates on second run)."""
    from app.models.family import Family

    family = Family(name="TestFamily")
    db_session.add(family)
    db_session.commit()

    user = User(
        family_id=family.id,
        role=UserRole.PARENT_ADMIN,
        display_name="Parent",
        email="upsert@example.ch",
    )
    db_session.add(user)
    db_session.commit()

    creds_dict = {
        "token": "test-token",
        "refresh_token": "test-refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "test-id",
        "client_secret": "test-secret",
        "scopes": ["https://www.googleapis.com/auth/calendar.readonly"],
    }
    account = CalendarSyncAccount(
        user_id=user.id,
        provider="google",
        oauth_token_encrypted=encrypt_secret(json.dumps(creds_dict)),
    )
    db_session.add(account)
    db_session.commit()

    with patch("app.worker.sync_worker.SessionLocal") as mock_session_local:
        mock_session_local.return_value = db_session
        db_session.close = MagicMock()  # Don't actually close the test session

        with patch("app.worker.sync_worker._build_and_refresh_credentials") as mock_build_creds:
            mock_creds = MagicMock()
            mock_build_creds.return_value = mock_creds

            with patch("app.worker.sync_worker.discovery.build") as mock_build:
                mock_service = MagicMock()
                mock_build.return_value = mock_service

                now = datetime.now(UTC)
                mock_service.events.return_value.list.return_value.execute.return_value = {
                    "items": [
                        {
                            "id": "event1",
                            "summary": "Original Title",
                            "start": {"dateTime": (now + timedelta(hours=1)).isoformat()},
                            "end": {"dateTime": (now + timedelta(hours=2)).isoformat()},
                        },
                    ]
                }

                family_id = family.id
                # Run sync first time
                sync_google_calendars()
                events = db_session.scalars(
                    select(CalendarEvent).where(CalendarEvent.family_id == family_id)
                ).all()
                assert len(events) == 1
                assert events[0].title == "Original Title"

                # Simulate event title update from Google
                mock_service.events.return_value.list.return_value.execute.return_value = {
                    "items": [
                        {
                            "id": "event1",
                            "summary": "Updated Title",
                            "start": {"dateTime": (now + timedelta(hours=1)).isoformat()},
                            "end": {"dateTime": (now + timedelta(hours=2)).isoformat()},
                        },
                    ]
                }

                # Run sync second time
                sync_google_calendars()

                # Verify event was updated, not duplicated
                events = db_session.scalars(
                    select(CalendarEvent).where(CalendarEvent.family_id == family_id)
                ).all()
                assert len(events) == 1
                assert events[0].title == "Updated Title"


def test_sync_google_calendars_continues_on_error(db_session):
    """Test that sync_google_calendars continues to next account on error."""
    from app.models.family import Family

    family = Family(name="TestFamily")
    db_session.add(family)
    db_session.commit()

    user1 = User(
        family_id=family.id,
        role=UserRole.PARENT_ADMIN,
        display_name="Parent1",
        email="error1@example.ch",
    )
    user2 = User(
        family_id=family.id,
        role=UserRole.PARENT_ADMIN,
        display_name="Parent2",
        email="error2@example.ch",
    )
    db_session.add_all([user1, user2])
    db_session.commit()

    creds_dict = {
        "token": "test-token",
        "refresh_token": "test-refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "test-id",
        "client_secret": "test-secret",
        "scopes": ["https://www.googleapis.com/auth/calendar.readonly"],
    }

    # First account has bad encrypted token
    account1 = CalendarSyncAccount(
        user_id=user1.id,
        provider="google",
        oauth_token_encrypted="invalid-encrypted-data",
    )
    # Second account is good
    account2 = CalendarSyncAccount(
        user_id=user2.id,
        provider="google",
        oauth_token_encrypted=encrypt_secret(json.dumps(creds_dict)),
    )
    db_session.add_all([account1, account2])
    db_session.commit()

    with patch("app.worker.sync_worker.SessionLocal") as mock_session_local:
        mock_session_local.return_value = db_session
        db_session.close = MagicMock()  # Don't actually close the test session

        with patch("app.worker.sync_worker._build_and_refresh_credentials") as mock_build_creds:
            def build_creds_side_effect(acct, sess):
                if acct.id == account1.id:
                    return None  # Simulate failed decryption
                else:
                    mock_creds = MagicMock()
                    return mock_creds

            mock_build_creds.side_effect = build_creds_side_effect

            with patch("app.worker.sync_worker.discovery.build") as mock_build:
                mock_service = MagicMock()
                mock_build.return_value = mock_service

                now = datetime.now(UTC)
                mock_service.events.return_value.list.return_value.execute.return_value = {
                    "items": [
                        {
                            "id": "event1",
                            "summary": "Event from Account 2",
                            "start": {"dateTime": (now + timedelta(hours=1)).isoformat()},
                            "end": {"dateTime": (now + timedelta(hours=2)).isoformat()},
                        },
                    ]
                }

                # Run sync - should skip account1 and process account2
                sync_google_calendars()

                # Verify only account2 was synced (event was created)
                events = db_session.scalars(select(CalendarEvent)).all()
                assert len(events) == 1
                assert events[0].title == "Event from Account 2"
