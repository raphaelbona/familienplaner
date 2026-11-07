import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import requests
from sqlalchemy import select

from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.calendar import CalendarEvent, CalendarSyncAccount
from app.models.user import User, UserRole
from app.worker.sync_worker import sync_caldav_calendars


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


def test_caldav_connect_requires_authentication(client):
    """Test that caldav_connect requires a valid Bearer token."""
    response = client.post(
        "/calendar/sync/caldav",
        json={
            "server_url": "https://example.com/caldav",
            "username": "user",
            "password": "pass",
        },
    )
    assert response.status_code == 401


def test_caldav_connect_returns_400_on_connection_failure(client):
    """Test that caldav_connect returns 400 when server connection fails."""
    headers = _register_parent(client, email="test@example.ch")

    with patch("app.api.calendar.caldav.DAVClient") as mock_client_class:
        # Simulate connection failure
        mock_client_class.side_effect = Exception("Connection refused")

        response = client.post(
            "/calendar/sync/caldav",
            headers=headers,
            json={
                "server_url": "https://example.com/caldav",
                "username": "user",
                "password": "pass",
            },
        )
        assert response.status_code == 400
        assert "Verbindung fehlgeschlagen" in response.json()["detail"]


def test_caldav_connect_returns_400_on_no_calendars(client):
    """Test that caldav_connect returns 400 when no calendars are found."""
    headers = _register_parent(client, email="nocal@example.ch")

    with patch("app.api.calendar.caldav.DAVClient") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Mock principal
        mock_principal = MagicMock()
        mock_client.principal.return_value = mock_principal

        # Return empty calendar list
        mock_principal.calendars.return_value = []

        response = client.post(
            "/calendar/sync/caldav",
            headers=headers,
            json={
                "server_url": "https://example.com/caldav",
                "username": "user",
                "password": "pass",
            },
        )
        assert response.status_code == 400
        assert "Keine Kalender" in response.json()["detail"]


def test_caldav_connect_creates_calendar_sync_account(client, db_session):
    """Test that caldav_connect creates a CalendarSyncAccount with encrypted token."""
    with patch("app.api.calendar.caldav.DAVClient") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Mock principal
        mock_principal = MagicMock()
        mock_client.principal.return_value = mock_principal

        # Mock calendar
        mock_calendar = MagicMock()
        mock_calendar.url = "https://example.com/caldav/user/calendar.ics"
        mock_principal.calendars.return_value = [mock_calendar]

        # Register a user and get token
        register_response = client.post(
            "/auth/register-family",
            json={
                "family_name": "Muster",
                "admin_display_name": "Papa",
                "admin_email": "caldav2@example.ch",
                "admin_password": "s3hr-sicher",
            },
        )
        token = register_response.json()["access_token"]
        user2 = db_session.scalar(select(User).where(User.email == "caldav2@example.ch"))

        response = client.post(
            "/calendar/sync/caldav",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "server_url": "https://example.com/caldav",
                "username": "testuser",
                "password": "testpass",
            },
        )

        assert response.status_code == 200
        assert "verbunden" in response.json()["detail"]

        # Verify CalendarSyncAccount was created
        account = db_session.scalar(
            select(CalendarSyncAccount).where(
                (CalendarSyncAccount.user_id == user2.id) & (CalendarSyncAccount.provider == "caldav")
            )
        )
        assert account is not None
        assert account.oauth_token_encrypted is not None
        assert account.calendar_ref == "https://example.com/caldav/user/calendar.ics"

        # Verify token is actually encrypted (not plaintext)
        assert "testpass" not in account.oauth_token_encrypted

        # Verify we can decrypt the token
        decrypted = decrypt_secret(account.oauth_token_encrypted)
        creds_dict = json.loads(decrypted)
        assert creds_dict["server_url"] == "https://example.com/caldav"
        assert creds_dict["username"] == "testuser"
        assert creds_dict["password"] == "testpass"


def test_caldav_connect_upserts_existing_account(client, db_session):
    """Test that caldav_connect updates an existing CalendarSyncAccount."""
    # Register user and create existing account
    register_response = client.post(
        "/auth/register-family",
        json={
            "family_name": "Muster",
            "admin_display_name": "Papa",
            "admin_email": "upsert@example.ch",
            "admin_password": "s3hr-sicher",
        },
    )
    token = register_response.json()["access_token"]
    user = db_session.scalar(select(User).where(User.email == "upsert@example.ch"))

    # Create an existing account
    existing_account = CalendarSyncAccount(
        user_id=user.id,
        provider="caldav",
        oauth_token_encrypted=encrypt_secret(
            json.dumps(
                {
                    "server_url": "https://old.example.com",
                    "username": "olduser",
                    "password": "oldpass",
                }
            )
        ),
        calendar_ref="https://old.example.com/calendar.ics",
    )
    db_session.add(existing_account)
    db_session.commit()

    with patch("app.api.calendar.caldav.DAVClient") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_principal = MagicMock()
        mock_client.principal.return_value = mock_principal

        mock_calendar = MagicMock()
        mock_calendar.url = "https://new.example.com/calendar.ics"
        mock_principal.calendars.return_value = [mock_calendar]

        response = client.post(
            "/calendar/sync/caldav",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "server_url": "https://new.example.com",
                "username": "newuser",
                "password": "newpass",
            },
        )

        assert response.status_code == 200

        # Verify the account was updated (not duplicated)
        accounts = db_session.scalars(
            select(CalendarSyncAccount).where(
                (CalendarSyncAccount.user_id == user.id)
                & (CalendarSyncAccount.provider == "caldav")
            )
        ).all()
        assert len(accounts) == 1

        # Verify token was updated
        decrypted = decrypt_secret(accounts[0].oauth_token_encrypted)
        creds_dict = json.loads(decrypted)
        assert creds_dict["username"] == "newuser"
        assert creds_dict["server_url"] == "https://new.example.com"


def test_sync_caldav_calendars_creates_events(db_session):
    """Test that sync_caldav_calendars creates CalendarEvent rows from CalDAV response."""
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
        "server_url": "https://example.com/caldav",
        "username": "testuser",
        "password": "testpass",
    }
    account = CalendarSyncAccount(
        user_id=user.id,
        provider="caldav",
        oauth_token_encrypted=encrypt_secret(json.dumps(creds_dict)),
        calendar_ref="https://example.com/caldav/user/calendar.ics",
    )
    db_session.add(account)
    db_session.commit()

    # Create mock iCalendar events
    now = datetime.now(UTC)

    def create_mock_event(uid, summary, dtstart, dtend, is_all_day=False):
        mock_event = MagicMock()
        mock_event.__getitem__ = lambda self, key: {
            "UID": uid,
            "SUMMARY": summary,
            "DTSTART": MagicMock(dt=dtstart),
            "DTEND": MagicMock(dt=dtend),
        }.get(key, MagicMock())
        mock_event.get = lambda key, default=None: {
            "UID": uid,
            "SUMMARY": summary,
            "DTSTART": MagicMock(dt=dtstart),
            "DTEND": MagicMock(dt=dtend),
        }.get(key, default)
        return mock_event

    # Mock event resources
    mock_event1_resource = MagicMock()
    mock_event1_resource.icalendar_component = create_mock_event(
        "event1",
        "Meeting",
        now + timedelta(hours=1),
        now + timedelta(hours=2),
    )

    mock_event2_resource = MagicMock()
    mock_event2_resource.icalendar_component = create_mock_event(
        "event2",
        "All Day Event",
        (now.date()),
        (now + timedelta(days=1)).date(),
    )

    mock_event3_resource = MagicMock()
    mock_event3_ical = create_mock_event(
        "event3",
        "No Title Event",
        now + timedelta(days=1),
        now + timedelta(days=1, hours=1),
    )
    # Test missing summary
    mock_event3_ical.get = lambda key, default=None: {
        "UID": "event3",
        "DTSTART": MagicMock(dt=now + timedelta(days=1)),
        "DTEND": MagicMock(dt=now + timedelta(days=1, hours=1)),
    }.get(key, default)
    mock_event3_resource.icalendar_component = mock_event3_ical

    with patch("app.worker.sync_worker.SessionLocal") as mock_session_local:
        mock_session_local.return_value = db_session
        db_session.close = MagicMock()

        with patch("app.worker.sync_worker.caldav.DAVClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            with patch("app.worker.sync_worker.caldav.Calendar") as mock_calendar_class:
                mock_calendar = MagicMock()
                mock_calendar_class.return_value = mock_calendar

                # Return mock event resources from date_search
                mock_calendar.date_search.return_value = [
                    mock_event1_resource,
                    mock_event2_resource,
                    mock_event3_resource,
                ]

                family_id = family.id
                account_id = account.id
                sync_caldav_calendars()

                # Verify events were created
                events = db_session.scalars(
                    select(CalendarEvent).where(CalendarEvent.family_id == family_id)
                ).all()
                assert len(events) == 3

                # Verify event details
                event1 = next((e for e in events if e.external_id == "event1"), None)
                assert event1 is not None
                assert event1.title == "Meeting"
                assert event1.source == "caldav"
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


def test_sync_caldav_calendars_upserts_events(db_session):
    """Test that sync_caldav_calendars upserts events (no duplicates on second run)."""
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
        "server_url": "https://example.com/caldav",
        "username": "testuser",
        "password": "testpass",
    }
    account = CalendarSyncAccount(
        user_id=user.id,
        provider="caldav",
        oauth_token_encrypted=encrypt_secret(json.dumps(creds_dict)),
        calendar_ref="https://example.com/caldav/user/calendar.ics",
    )
    db_session.add(account)
    db_session.commit()

    now = datetime.now(UTC)

    def create_mock_event(uid, summary, dtstart, dtend):
        mock_event = MagicMock()
        mock_event.__getitem__ = lambda self, key: {
            "UID": uid,
            "SUMMARY": summary,
            "DTSTART": MagicMock(dt=dtstart),
            "DTEND": MagicMock(dt=dtend),
        }.get(key, MagicMock())
        mock_event.get = lambda key, default=None: {
            "UID": uid,
            "SUMMARY": summary,
            "DTSTART": MagicMock(dt=dtstart),
            "DTEND": MagicMock(dt=dtend),
        }.get(key, default)
        return mock_event

    with patch("app.worker.sync_worker.SessionLocal") as mock_session_local:
        mock_session_local.return_value = db_session
        db_session.close = MagicMock()

        with patch("app.worker.sync_worker.caldav.DAVClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            with patch("app.worker.sync_worker.caldav.Calendar") as mock_calendar_class:
                mock_calendar = MagicMock()
                mock_calendar_class.return_value = mock_calendar

                # First sync
                mock_event_resource = MagicMock()
                mock_event_resource.icalendar_component = create_mock_event(
                    "event1",
                    "Original Title",
                    now + timedelta(hours=1),
                    now + timedelta(hours=2),
                )
                mock_calendar.date_search.return_value = [mock_event_resource]

                family_id = family.id
                sync_caldav_calendars()

                events = db_session.scalars(
                    select(CalendarEvent).where(CalendarEvent.family_id == family_id)
                ).all()
                assert len(events) == 1
                assert events[0].title == "Original Title"

                # Second sync with updated title
                mock_event_resource.icalendar_component = create_mock_event(
                    "event1",
                    "Updated Title",
                    now + timedelta(hours=1),
                    now + timedelta(hours=2),
                )
                mock_calendar.date_search.return_value = [mock_event_resource]

                sync_caldav_calendars()

                # Verify event was updated, not duplicated
                events = db_session.scalars(
                    select(CalendarEvent).where(CalendarEvent.family_id == family_id)
                ).all()
                assert len(events) == 1
                assert events[0].title == "Updated Title"


def test_sync_caldav_calendars_continues_on_error(db_session):
    """Test that sync_caldav_calendars continues to next account on error."""
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
        "server_url": "https://example.com/caldav",
        "username": "testuser",
        "password": "testpass",
    }

    # First account has bad encrypted token
    account1 = CalendarSyncAccount(
        user_id=user1.id,
        provider="caldav",
        oauth_token_encrypted="invalid-encrypted-data",
        calendar_ref="https://example.com/caldav/user1/calendar.ics",
    )
    # Second account is good
    account2 = CalendarSyncAccount(
        user_id=user2.id,
        provider="caldav",
        oauth_token_encrypted=encrypt_secret(json.dumps(creds_dict)),
        calendar_ref="https://example.com/caldav/user2/calendar.ics",
    )
    db_session.add_all([account1, account2])
    db_session.commit()

    now = datetime.now(UTC)

    def create_mock_event(uid, summary, dtstart, dtend):
        mock_event = MagicMock()
        mock_event.__getitem__ = lambda self, key: {
            "UID": uid,
            "SUMMARY": summary,
            "DTSTART": MagicMock(dt=dtstart),
            "DTEND": MagicMock(dt=dtend),
        }.get(key, MagicMock())
        mock_event.get = lambda key, default=None: {
            "UID": uid,
            "SUMMARY": summary,
            "DTSTART": MagicMock(dt=dtstart),
            "DTEND": MagicMock(dt=dtend),
        }.get(key, default)
        return mock_event

    with patch("app.worker.sync_worker.SessionLocal") as mock_session_local:
        mock_session_local.return_value = db_session
        db_session.close = MagicMock()

        with patch("app.worker.sync_worker.caldav.DAVClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            with patch("app.worker.sync_worker.caldav.Calendar") as mock_calendar_class:
                mock_calendar = MagicMock()
                mock_calendar_class.return_value = mock_calendar

                mock_event_resource = MagicMock()
                mock_event_resource.icalendar_component = create_mock_event(
                    "event1",
                    "Event from Account 2",
                    now + timedelta(hours=1),
                    now + timedelta(hours=2),
                )
                mock_calendar.date_search.return_value = [mock_event_resource]

                # Run sync - should skip account1 and process account2
                sync_caldav_calendars()

                # Verify only account2 was synced (event was created)
                events = db_session.scalars(select(CalendarEvent)).all()
                assert len(events) == 1
                assert events[0].title == "Event from Account 2"


def test_sync_caldav_calendars_tenancy_isolation(db_session):
    """Test that sync_caldav_calendars correctly scopes events to the right family."""
    from app.models.family import Family

    family1 = Family(name="Family1")
    family2 = Family(name="Family2")
    db_session.add_all([family1, family2])
    db_session.commit()

    user1 = User(
        family_id=family1.id,
        role=UserRole.PARENT_ADMIN,
        display_name="Parent1",
        email="user1@example.ch",
    )
    user2 = User(
        family_id=family2.id,
        role=UserRole.PARENT_ADMIN,
        display_name="Parent2",
        email="user2@example.ch",
    )
    db_session.add_all([user1, user2])
    db_session.commit()

    creds_dict1 = {
        "server_url": "https://example1.com/caldav",
        "username": "user1",
        "password": "pass1",
    }
    creds_dict2 = {
        "server_url": "https://example2.com/caldav",
        "username": "user2",
        "password": "pass2",
    }

    account1 = CalendarSyncAccount(
        user_id=user1.id,
        provider="caldav",
        oauth_token_encrypted=encrypt_secret(json.dumps(creds_dict1)),
        calendar_ref="https://example1.com/caldav/user1/calendar.ics",
    )
    account2 = CalendarSyncAccount(
        user_id=user2.id,
        provider="caldav",
        oauth_token_encrypted=encrypt_secret(json.dumps(creds_dict2)),
        calendar_ref="https://example2.com/caldav/user2/calendar.ics",
    )
    db_session.add_all([account1, account2])
    db_session.commit()

    now = datetime.now(UTC)

    def create_mock_event(uid, summary, dtstart, dtend):
        mock_event = MagicMock()
        mock_event.__getitem__ = lambda self, key: {
            "UID": uid,
            "SUMMARY": summary,
            "DTSTART": MagicMock(dt=dtstart),
            "DTEND": MagicMock(dt=dtend),
        }.get(key, MagicMock())
        mock_event.get = lambda key, default=None: {
            "UID": uid,
            "SUMMARY": summary,
            "DTSTART": MagicMock(dt=dtstart),
            "DTEND": MagicMock(dt=dtend),
        }.get(key, default)
        return mock_event

    with patch("app.worker.sync_worker.SessionLocal") as mock_session_local:
        mock_session_local.return_value = db_session
        db_session.close = MagicMock()

        with patch("app.worker.sync_worker.caldav.DAVClient") as mock_client_class:
            call_count = [0]

            def create_client(*args, **kwargs):
                client = MagicMock()
                call_count[0] += 1
                return client

            mock_client_class.side_effect = create_client

            with patch("app.worker.sync_worker.caldav.Calendar") as mock_calendar_class:
                calendar_call_count = [0]

                def create_calendar(*args, **kwargs):
                    calendar = MagicMock()
                    calendar_call_count[0] += 1

                    if calendar_call_count[0] == 1:
                        # First calendar (family1)
                        event_resource = MagicMock()
                        event_resource.icalendar_component = create_mock_event(
                            "event1",
                            "Family1 Event",
                            now + timedelta(hours=1),
                            now + timedelta(hours=2),
                        )
                        calendar.date_search.return_value = [event_resource]
                    else:
                        # Second calendar (family2)
                        event_resource = MagicMock()
                        event_resource.icalendar_component = create_mock_event(
                            "event2",
                            "Family2 Event",
                            now + timedelta(hours=1),
                            now + timedelta(hours=2),
                        )
                        calendar.date_search.return_value = [event_resource]

                    return calendar

                mock_calendar_class.side_effect = create_calendar

                sync_caldav_calendars()

                # Verify events are scoped correctly
                family1_events = db_session.scalars(
                    select(CalendarEvent).where(CalendarEvent.family_id == family1.id)
                ).all()
                family2_events = db_session.scalars(
                    select(CalendarEvent).where(CalendarEvent.family_id == family2.id)
                ).all()

                assert len(family1_events) == 1
                assert family1_events[0].title == "Family1 Event"

                assert len(family2_events) == 1
                assert family2_events[0].title == "Family2 Event"


def test_caldav_connect_passes_timeout_parameter(client):
    """Test that caldav_connect passes timeout=10 to DAVClient."""
    headers = _register_parent(client, email="timeout-check@example.ch")

    with patch("app.api.calendar.caldav.DAVClient") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_principal = MagicMock()
        mock_client.principal.return_value = mock_principal

        mock_calendar = MagicMock()
        mock_calendar.url = "https://example.com/caldav/calendar.ics"
        mock_principal.calendars.return_value = [mock_calendar]

        response = client.post(
            "/calendar/sync/caldav",
            headers=headers,
            json={
                "server_url": "https://example.com/caldav",
                "username": "testuser",
                "password": "testpass",
            },
        )

        assert response.status_code == 200

        # Verify DAVClient was called with timeout=10
        mock_client_class.assert_called_once()
        call_kwargs = mock_client_class.call_args[1]
        assert "timeout" in call_kwargs
        assert call_kwargs["timeout"] == 10


def test_caldav_connect_returns_400_on_timeout(client):
    """Test that caldav_connect returns 400 when CalDAV server times out."""
    headers = _register_parent(client, email="timeout-test@example.ch")

    with patch("app.api.calendar.caldav.DAVClient") as mock_client_class:
        # Simulate timeout exception (requests.exceptions.Timeout or niquests equivalent)
        mock_client_class.side_effect = requests.exceptions.Timeout("Connection timed out")

        response = client.post(
            "/calendar/sync/caldav",
            headers=headers,
            json={
                "server_url": "https://example.com/caldav",
                "username": "user",
                "password": "pass",
            },
        )
        assert response.status_code == 400
        assert "Verbindung fehlgeschlagen" in response.json()["detail"]
