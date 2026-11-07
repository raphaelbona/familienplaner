"""Tests for the timetables API."""

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.calendar import CalendarEvent
from app.models.timetable import TimetableEntry
from app.models.user import UserRole


@pytest.fixture(autouse=True)
def _patch_upload_dir(tmp_path):
    """Patch storage settings to use a temporary directory for all tests in this module."""
    with patch("app.core.storage.get_settings") as mock_settings:
        mock_settings.return_value.upload_dir = str(tmp_path)
        yield


def _register_parent(client, email="eltern@example.ch"):
    """Register a parent and return the authorization headers."""
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


def test_upload_timetable_successful(client, db_session):
    """Test successful timetable upload with mocked vision response."""
    parent_headers = _register_parent(client, email="parent1@example.ch")

    # Get the parent to find the family_id
    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    # Create a child manually in the database to avoid API endpoint issues
    from app.models.user import User as UserModel
    child = UserModel(
        family_id=family_id,
        display_name="Kind",
        email=None,
        password_hash="",
        role=UserRole.CHILD,
        pin_hash="",
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    child_id = child.id

    # Mock the vision module
    mocked_entries = [
        {"day_of_week": 0, "start_time": "08:00", "end_time": "09:00", "subject": "Deutsch", "room": "101"},
        {"day_of_week": 0, "start_time": "09:00", "end_time": "10:00", "subject": "Mathe", "room": None},
        {"day_of_week": 1, "start_time": "08:00", "end_time": "09:00", "subject": "Englisch", "room": "102"},
    ]

    with patch("app.api.timetables.interpret_timetable_image") as mock_vision:
        mock_vision.return_value = mocked_entries

        # Upload a timetable
        response = client.post(
            "/timetables/upload",
            data={"child_user_id": str(child_id)},
            files={"file": ("schedule.png", b"fake image content", "image/png")},
            headers=parent_headers,
        )

    assert response.status_code == 201
    entries = response.json()
    assert len(entries) == 3
    assert entries[0]["subject"] == "Deutsch"
    assert entries[1]["subject"] == "Mathe"
    assert entries[2]["subject"] == "Englisch"

    # Verify entries are in the database
    db_entries = db_session.scalars(select(TimetableEntry).where(TimetableEntry.child_user_id == child_id)).all()
    assert len(db_entries) == 3


def test_upload_timetable_replaces_existing_entries(client, db_session):
    """Test that re-uploading replaces old entries."""
    parent_headers = _register_parent(client, email="parent2@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    # Create a child in the database
    from app.models.user import User as UserModel
    child = UserModel(
        family_id=family_id,
        display_name="Kind",
        email=None,
        password_hash="",
        role=UserRole.CHILD,
        pin_hash="",
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    child_id = child.id

    # First upload
    mocked_entries_1 = [
        {"day_of_week": 0, "start_time": "08:00", "end_time": "09:00", "subject": "Deutsch", "room": "101"},
    ]

    with patch("app.api.timetables.interpret_timetable_image") as mock_vision:
        mock_vision.return_value = mocked_entries_1

        response = client.post(
            "/timetables/upload",
            data={"child_user_id": str(child_id)},
            files={"file": ("schedule.png", b"fake image 1", "image/png")},
            headers=parent_headers,
        )
    assert response.status_code == 201
    assert len(response.json()) == 1

    # Second upload with different entries
    mocked_entries_2 = [
        {"day_of_week": 0, "start_time": "09:00", "end_time": "10:00", "subject": "Mathe", "room": "202"},
        {"day_of_week": 1, "start_time": "10:00", "end_time": "11:00", "subject": "Englisch", "room": "203"},
    ]

    with patch("app.api.timetables.interpret_timetable_image") as mock_vision:
        mock_vision.return_value = mocked_entries_2

        response = client.post(
            "/timetables/upload",
            data={"child_user_id": str(child_id)},
            files={"file": ("schedule.png", b"fake image 2", "image/png")},
            headers=parent_headers,
        )
    assert response.status_code == 201
    assert len(response.json()) == 2

    # Verify only new entries exist (old ones were replaced)
    db_entries = db_session.scalars(select(TimetableEntry).where(TimetableEntry.child_user_id == child_id)).all()
    assert len(db_entries) == 2
    subjects = {entry.subject for entry in db_entries}
    assert subjects == {"Mathe", "Englisch"}


def test_upload_timetable_generates_calendar_events(client, db_session):
    """Uploading a timetable should auto-generate recurring calendar events for the child."""
    parent_headers = _register_parent(client, email="calendar_gen@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    from app.models.user import User as UserModel
    child = UserModel(
        family_id=family_id,
        display_name="Kind",
        email=None,
        password_hash="",
        role=UserRole.CHILD,
        pin_hash="",
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    child_id = child.id

    # Monday lesson
    mocked_entries = [
        {"day_of_week": 0, "start_time": "08:00", "end_time": "09:00", "subject": "Deutsch", "room": "101"},
    ]

    with patch("app.api.timetables.interpret_timetable_image") as mock_vision:
        mock_vision.return_value = mocked_entries

        response = client.post(
            "/timetables/upload",
            data={"child_user_id": str(child_id)},
            files={"file": ("schedule.png", b"fake image content", "image/png")},
            headers=parent_headers,
        )
    assert response.status_code == 201

    events = db_session.scalars(
        select(CalendarEvent).where(CalendarEvent.owner_user_id == child_id, CalendarEvent.source == "timetable")
    ).all()

    # ~6 Mondays fall inside the 42-day rolling window
    assert len(events) >= 5
    assert all(event.start.weekday() == 0 for event in events)
    assert all("Deutsch" in event.title for event in events)
    assert all("101" in event.title for event in events)


def test_reupload_timetable_replaces_calendar_events(client, db_session):
    """Re-uploading a timetable should replace, not append to, the generated calendar events."""
    parent_headers = _register_parent(client, email="calendar_replace@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    from app.models.user import User as UserModel
    child = UserModel(
        family_id=family_id,
        display_name="Kind",
        email=None,
        password_hash="",
        role=UserRole.CHILD,
        pin_hash="",
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    child_id = child.id

    with patch("app.api.timetables.interpret_timetable_image") as mock_vision:
        mock_vision.return_value = [
            {"day_of_week": 0, "start_time": "08:00", "end_time": "09:00", "subject": "Deutsch", "room": "101"},
        ]
        client.post(
            "/timetables/upload",
            data={"child_user_id": str(child_id)},
            files={"file": ("schedule.png", b"fake image 1", "image/png")},
            headers=parent_headers,
        )

    with patch("app.api.timetables.interpret_timetable_image") as mock_vision:
        mock_vision.return_value = [
            {"day_of_week": 2, "start_time": "10:00", "end_time": "11:00", "subject": "Sport", "room": None},
        ]
        client.post(
            "/timetables/upload",
            data={"child_user_id": str(child_id)},
            files={"file": ("schedule.png", b"fake image 2", "image/png")},
            headers=parent_headers,
        )

    events = db_session.scalars(
        select(CalendarEvent).where(CalendarEvent.owner_user_id == child_id, CalendarEvent.source == "timetable")
    ).all()

    assert len(events) > 0
    assert all(event.title == "Sport" for event in events)
    assert all(event.start.weekday() == 2 for event in events)


def test_upload_timetable_non_image_file_rejected(client, db_session):
    """Test that uploading a non-image file returns 400."""
    parent_headers = _register_parent(client, email="parent3@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    from app.models.user import User as UserModel
    child = UserModel(
        family_id=family_id,
        display_name="Kind",
        email=None,
        password_hash="",
        role=UserRole.CHILD,
        pin_hash="",
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    child_id = child.id

    response = client.post(
        "/timetables/upload",
        data={"child_user_id": str(child_id)},
        files={"file": ("document.txt", b"not an image", "text/plain")},
        headers=parent_headers,
    )

    assert response.status_code == 400
    assert "Nur Bilddateien werden unterstützt" in response.json()["detail"]


def test_upload_timetable_non_parent_rejected(client, db_session):
    """Test that a non-parent user cannot upload a timetable."""
    response = client.post(
        "/timetables/upload",
        data={"child_user_id": str(uuid.uuid4())},
        files={"file": ("schedule.png", b"fake image", "image/png")},
    )

    assert response.status_code == 401


def test_upload_timetable_api_key_missing(client, db_session):
    """Test that missing API key returns 503."""
    parent_headers = _register_parent(client, email="parent@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    from app.models.user import User as UserModel
    child = UserModel(
        family_id=family_id,
        display_name="Kind",
        email=None,
        password_hash="",
        role=UserRole.CHILD,
        pin_hash="",
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    child_id = child.id

    with patch("app.api.timetables.interpret_timetable_image") as mock_vision:
        mock_vision.side_effect = RuntimeError("Anthropic-API ist nicht konfiguriert.")

        response = client.post(
            "/timetables/upload",
            data={"child_user_id": str(child_id)},
            files={"file": ("schedule.png", b"fake image", "image/png")},
            headers=parent_headers,
        )

    assert response.status_code == 503
    assert "Anthropic-API" in response.json()["detail"]


def test_get_timetable(client, db_session):
    """Test getting a timetable."""
    parent_headers = _register_parent(client, email="parent@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    from app.models.user import User as UserModel
    child = UserModel(
        family_id=family_id,
        display_name="Kind",
        email=None,
        password_hash="",
        role=UserRole.CHILD,
        pin_hash="",
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    child_id = child.id

    # Upload a timetable
    mocked_entries = [
        {"day_of_week": 1, "start_time": "09:00", "end_time": "10:00", "subject": "Mathe", "room": "202"},
        {"day_of_week": 0, "start_time": "08:00", "end_time": "09:00", "subject": "Deutsch", "room": "101"},
    ]

    with patch("app.api.timetables.interpret_timetable_image") as mock_vision:
        mock_vision.return_value = mocked_entries

        response = client.post(
            "/timetables/upload",
            data={"child_user_id": str(child_id)},
            files={"file": ("schedule.png", b"fake image", "image/png")},
            headers=parent_headers,
        )
    assert response.status_code == 201

    # Get the timetable
    response = client.get(f"/timetables/{child_id}", headers=parent_headers)

    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == 2
    # Should be ordered by day_of_week, start_time
    assert entries[0]["day_of_week"] == 0
    assert entries[0]["start_time"] == "08:00"
    assert entries[1]["day_of_week"] == 1
    assert entries[1]["start_time"] == "09:00"


def test_get_timetable_empty(client, db_session):
    """Test getting an empty timetable."""
    parent_headers = _register_parent(client, email="parent@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    from app.models.user import User as UserModel
    child = UserModel(
        family_id=family_id,
        display_name="Kind",
        email=None,
        password_hash="",
        role=UserRole.CHILD,
        pin_hash="",
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    child_id = child.id

    # Get the timetable without uploading anything
    response = client.get(f"/timetables/{child_id}", headers=parent_headers)

    assert response.status_code == 200
    entries = response.json()
    assert entries == []


def test_upload_timetable_anthropic_api_error_returns_503(client, db_session):
    """Test that Anthropic API errors are converted to 503 responses."""
    parent_headers = _register_parent(client, email="parent4@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    from app.models.user import User as UserModel
    child = UserModel(
        family_id=family_id,
        display_name="Kind",
        email=None,
        password_hash="",
        role=UserRole.CHILD,
        pin_hash="",
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    child_id = child.id

    # Mock interpret_timetable_image to raise a RuntimeError
    # (simulating an Anthropic API error that was caught and wrapped)
    with patch("app.api.timetables.interpret_timetable_image") as mock_vision:
        mock_vision.side_effect = RuntimeError("Anthropic-API-Fehler beim Verarbeiten des Bildes.")

        response = client.post(
            "/timetables/upload",
            data={"child_user_id": str(child_id)},
            files={"file": ("schedule.png", b"fake image", "image/png")},
            headers=parent_headers,
        )

    # Should return 503 Service Unavailable
    assert response.status_code == 503
    assert "Anthropic-API-Fehler" in response.json()["detail"]


def _create_child(client, db_session, family_id):
    from app.models.user import User as UserModel

    child = UserModel(
        family_id=family_id,
        display_name="Kind",
        email=None,
        password_hash="",
        role=UserRole.CHILD,
        pin_hash="",
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    return child.id


def test_create_timetable_entry_manually(client, db_session):
    """A parent can add a single timetable entry by hand, without an image upload."""
    parent_headers = _register_parent(client, email="manual1@example.ch")
    family_id = uuid.UUID(client.get("/families/me", headers=parent_headers).json()["id"])
    child_id = _create_child(client, db_session, family_id)

    response = client.post(
        "/timetables",
        json={
            "child_user_id": str(child_id),
            "day_of_week": 0,
            "start_time": "07:30",
            "end_time": "08:15",
            "subject": "MU",
            "room": "G 0.01",
        },
        headers=parent_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["subject"] == "MU"
    assert body["room"] == "G 0.01"

    db_entries = db_session.scalars(select(TimetableEntry).where(TimetableEntry.child_user_id == child_id)).all()
    assert len(db_entries) == 1

    events = db_session.scalars(
        select(CalendarEvent).where(CalendarEvent.owner_user_id == child_id, CalendarEvent.source == "timetable")
    ).all()
    assert len(events) > 0


def test_create_timetable_entry_invalid_time_rejected(client, db_session):
    """Malformed times are rejected with a validation error, not silently stored."""
    parent_headers = _register_parent(client, email="manual2@example.ch")
    family_id = uuid.UUID(client.get("/families/me", headers=parent_headers).json()["id"])
    child_id = _create_child(client, db_session, family_id)

    response = client.post(
        "/timetables",
        json={
            "child_user_id": str(child_id),
            "day_of_week": 0,
            "start_time": "7:30",
            "end_time": "08:15",
            "subject": "MU",
        },
        headers=parent_headers,
    )

    assert response.status_code == 422


def test_create_timetable_entry_for_child_in_other_family_rejected(client, db_session):
    """A parent cannot add entries for a child outside their own family."""
    _register_parent(client, email="manual3a@example.ch")
    other_headers = _register_parent(client, email="manual3b@example.ch")
    other_family_id = uuid.UUID(client.get("/families/me", headers=other_headers).json()["id"])
    other_child_id = _create_child(client, db_session, other_family_id)

    # Log back in as the first parent to attempt cross-family access.
    login_response = client.post(
        "/auth/login", json={"email": "manual3a@example.ch", "password": "s3hr-sicher"}
    )
    parent_headers = {"Authorization": f"Bearer {login_response.json()['access_token']}"}

    response = client.post(
        "/timetables",
        json={
            "child_user_id": str(other_child_id),
            "day_of_week": 0,
            "start_time": "07:30",
            "end_time": "08:15",
            "subject": "MU",
        },
        headers=parent_headers,
    )

    assert response.status_code == 404


def test_update_timetable_entry(client, db_session):
    """A parent can edit an existing entry's fields."""
    parent_headers = _register_parent(client, email="manual4@example.ch")
    family_id = uuid.UUID(client.get("/families/me", headers=parent_headers).json()["id"])
    child_id = _create_child(client, db_session, family_id)

    create_response = client.post(
        "/timetables",
        json={
            "child_user_id": str(child_id),
            "day_of_week": 0,
            "start_time": "07:30",
            "end_time": "08:15",
            "subject": "MU",
            "room": "G 0.01",
        },
        headers=parent_headers,
    )
    entry_id = create_response.json()["id"]

    response = client.patch(
        f"/timetables/{entry_id}",
        json={"subject": "Musik", "room": "G 1.05"},
        headers=parent_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "Musik"
    assert body["room"] == "G 1.05"
    assert body["start_time"] == "07:30"


def test_update_timetable_entry_not_found(client, db_session):
    """Editing a nonexistent entry returns 404."""
    parent_headers = _register_parent(client, email="manual5@example.ch")

    response = client.patch(
        f"/timetables/{uuid.uuid4()}",
        json={"subject": "Musik"},
        headers=parent_headers,
    )

    assert response.status_code == 404


def test_delete_timetable_entry(client, db_session):
    """A parent can delete a single manually-created entry."""
    parent_headers = _register_parent(client, email="manual6@example.ch")
    family_id = uuid.UUID(client.get("/families/me", headers=parent_headers).json()["id"])
    child_id = _create_child(client, db_session, family_id)

    create_response = client.post(
        "/timetables",
        json={
            "child_user_id": str(child_id),
            "day_of_week": 0,
            "start_time": "07:30",
            "end_time": "08:15",
            "subject": "MU",
        },
        headers=parent_headers,
    )
    entry_id = create_response.json()["id"]

    response = client.delete(f"/timetables/{entry_id}", headers=parent_headers)
    assert response.status_code == 204

    db_entries = db_session.scalars(select(TimetableEntry).where(TimetableEntry.child_user_id == child_id)).all()
    assert len(db_entries) == 0

    events = db_session.scalars(
        select(CalendarEvent).where(CalendarEvent.owner_user_id == child_id, CalendarEvent.source == "timetable")
    ).all()
    assert len(events) == 0


def test_create_and_delete_timetable_entry_non_parent_rejected(client, db_session):
    """Non-parent users cannot create or delete timetable entries."""
    response = client.post(
        "/timetables",
        json={
            "child_user_id": str(uuid.uuid4()),
            "day_of_week": 0,
            "start_time": "07:30",
            "end_time": "08:15",
            "subject": "MU",
        },
    )
    assert response.status_code == 401

    response = client.delete(f"/timetables/{uuid.uuid4()}")
    assert response.status_code == 401


def test_bulk_assign_creates_new_entries(client, db_session):
    """The week-grid tool can assign one subject to several new slots at once."""
    parent_headers = _register_parent(client, email="bulk1@example.ch")
    family_id = uuid.UUID(client.get("/families/me", headers=parent_headers).json()["id"])
    child_id = _create_child(client, db_session, family_id)

    response = client.post(
        "/timetables/bulk-assign",
        json={
            "child_user_id": str(child_id),
            "subject": "M",
            "room": "G 1.05",
            "slots": [
                {"day_of_week": 0, "start_time": "09:10", "end_time": "09:55"},
                {"day_of_week": 1, "start_time": "08:20", "end_time": "09:05"},
                {"day_of_week": 3, "start_time": "10:15", "end_time": "11:00"},
            ],
        },
        headers=parent_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3
    assert all(entry["subject"] == "M" and entry["room"] == "G 1.05" for entry in body)

    db_entries = db_session.scalars(select(TimetableEntry).where(TimetableEntry.child_user_id == child_id)).all()
    assert len(db_entries) == 3

    events = db_session.scalars(
        select(CalendarEvent).where(CalendarEvent.owner_user_id == child_id, CalendarEvent.source == "timetable")
    ).all()
    assert len(events) > 0


def test_bulk_assign_overwrites_existing_slot(client, db_session):
    """Re-assigning a slot that already has an entry updates it in place instead of duplicating it."""
    parent_headers = _register_parent(client, email="bulk2@example.ch")
    family_id = uuid.UUID(client.get("/families/me", headers=parent_headers).json()["id"])
    child_id = _create_child(client, db_session, family_id)

    client.post(
        "/timetables/bulk-assign",
        json={
            "child_user_id": str(child_id),
            "subject": "M",
            "slots": [{"day_of_week": 0, "start_time": "09:10", "end_time": "09:55"}],
        },
        headers=parent_headers,
    )

    response = client.post(
        "/timetables/bulk-assign",
        json={
            "child_user_id": str(child_id),
            "subject": "NMG",
            "room": "G 1.05",
            "slots": [{"day_of_week": 0, "start_time": "09:10", "end_time": "09:55"}],
        },
        headers=parent_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["subject"] == "NMG"
    assert body[0]["room"] == "G 1.05"

    db_entries = db_session.scalars(select(TimetableEntry).where(TimetableEntry.child_user_id == child_id)).all()
    assert len(db_entries) == 1
    assert db_entries[0].subject == "NMG"


def test_bulk_assign_for_child_in_other_family_rejected(client, db_session):
    """A parent cannot bulk-assign entries for a child outside their own family."""
    _register_parent(client, email="bulk3a@example.ch")
    other_headers = _register_parent(client, email="bulk3b@example.ch")
    other_family_id = uuid.UUID(client.get("/families/me", headers=other_headers).json()["id"])
    other_child_id = _create_child(client, db_session, other_family_id)

    login_response = client.post(
        "/auth/login", json={"email": "bulk3a@example.ch", "password": "s3hr-sicher"}
    )
    parent_headers = {"Authorization": f"Bearer {login_response.json()['access_token']}"}

    response = client.post(
        "/timetables/bulk-assign",
        json={
            "child_user_id": str(other_child_id),
            "subject": "M",
            "slots": [{"day_of_week": 0, "start_time": "09:10", "end_time": "09:55"}],
        },
        headers=parent_headers,
    )

    assert response.status_code == 404


def test_bulk_assign_non_parent_rejected(client, db_session):
    """Non-parent users cannot use the bulk-assign endpoint."""
    response = client.post(
        "/timetables/bulk-assign",
        json={
            "child_user_id": str(uuid.uuid4()),
            "subject": "M",
            "slots": [{"day_of_week": 0, "start_time": "09:10", "end_time": "09:55"}],
        },
    )
    assert response.status_code == 401


def test_bulk_assign_requires_at_least_one_slot(client, db_session):
    """An empty slot list is rejected with a validation error."""
    parent_headers = _register_parent(client, email="bulk4@example.ch")
    family_id = uuid.UUID(client.get("/families/me", headers=parent_headers).json()["id"])
    child_id = _create_child(client, db_session, family_id)

    response = client.post(
        "/timetables/bulk-assign",
        json={"child_user_id": str(child_id), "subject": "M", "slots": []},
        headers=parent_headers,
    )

    assert response.status_code == 422
