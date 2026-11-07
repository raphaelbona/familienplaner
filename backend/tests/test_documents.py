"""Tests for the documents API."""

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.core.security import hash_secret
from app.models.calendar import CalendarEvent
from app.models.user import User, UserRole


@pytest.fixture(autouse=True)
def _patch_upload_dir(tmp_path):
    """Patch storage settings to use a temporary directory for all tests in this module."""
    with patch("app.core.storage.get_settings") as mock_settings:
        mock_settings.return_value.upload_dir = str(tmp_path)
        yield


def _register_parent(client, email="eltern@example.ch"):
    """Register a parent and return authorization headers."""
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


def _create_child(db_session, family_id, display_name="Anna", pin="1234"):
    """Create a child user in the database."""
    child = User(
        family_id=family_id,
        role=UserRole.CHILD,
        display_name=display_name,
        pin_hash=hash_secret(pin),
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    return child


def test_upload_document_with_single_matching_child(client, db_session):
    """Upload document with event whose person_hint matches exactly one child."""
    parent_headers = _register_parent(client, email="parent1@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    # Create a child
    child = _create_child(db_session, family_id, display_name="Anna")

    # Mock extract_letter_events to return one event matching the child
    # Note: extract_letter_events returns JSON-style strings for dates/times
    mocked_events = [
        {
            "title": "Schulausflug",
            "event_date": "2026-09-15",
            "event_time": "10:00",
            "person_hint": "Anna",
        }
    ]

    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.return_value = mocked_events

        response = client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers,
        )

    assert response.status_code == 201
    doc = response.json()
    assert doc["status"] == "processed"
    assert len(doc["events"]) == 1
    assert doc["events"][0]["title"] == "Schulausflug"
    assert doc["events"][0]["status"] == "confirmed"
    assert doc["events"][0]["resolved_child_user_id"] == str(child.id)
    assert doc["events"][0]["calendar_event_id"] is not None

    # Verify calendar event was created
    cal_events = db_session.scalars(
        select(CalendarEvent).where(CalendarEvent.family_id == family_id, CalendarEvent.source == "parent_letter")
    ).all()
    assert len(cal_events) == 1
    assert cal_events[0].title == "Schulausflug"


def test_upload_document_with_null_person_hint(client, db_session):
    """Upload document with event that has no person_hint (needs clarification)."""
    parent_headers = _register_parent(client, email="parent2@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    # Create a child
    _create_child(db_session, family_id, display_name="Bob")

    # Mock extraction with null person_hint
    # Note: extract_letter_events returns JSON-style strings for dates
    mocked_events = [
        {
            "title": "Elternabend",
            "event_date": "2026-09-20",
            "event_time": None,
            "person_hint": None,
        }
    ]

    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.return_value = mocked_events

        response = client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers,
        )

    assert response.status_code == 201
    doc = response.json()
    assert doc["status"] == "needs_clarification"
    assert doc["events"][0]["status"] == "pending_clarification"
    assert doc["events"][0]["resolved_child_user_id"] is None
    assert doc["events"][0]["calendar_event_id"] is None


def test_upload_document_with_ambiguous_person_hint(client, db_session):
    """Upload document where person_hint matches multiple children."""
    parent_headers = _register_parent(client, email="parent3@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    # Create two children with overlapping names
    _create_child(db_session, family_id, display_name="Anna")
    _create_child(db_session, family_id, display_name="Anne")

    # Mock extraction with hint matching both
    # Note: extract_letter_events returns JSON-style strings for dates
    mocked_events = [
        {
            "title": "Hausaufgaben",
            "event_date": "2026-09-25",
            "event_time": None,
            "person_hint": "Ann",  # Matches both Anna and Anne
        }
    ]

    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.return_value = mocked_events

        response = client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers,
        )

    assert response.status_code == 201
    doc = response.json()
    assert doc["status"] == "needs_clarification"
    assert doc["events"][0]["status"] == "pending_clarification"


def test_list_documents_family_scoped(client, db_session):
    """List documents returns only current family's documents."""
    parent_headers_1 = _register_parent(client, email="parent1@example.ch")
    parent_headers_2 = _register_parent(client, email="parent2@example.ch")

    # Upload document in family 1
    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.return_value = []
        client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers_1,
        )

    # List documents as family 2
    response = client.get("/documents", headers=parent_headers_2)
    assert response.status_code == 200
    assert len(response.json()) == 0

    # List documents as family 1
    response = client.get("/documents", headers=parent_headers_1)
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_get_document_file(client, db_session):
    """Download document file returns correct content and headers."""
    parent_headers = _register_parent(client, email="parent@example.ch")

    # Upload a document
    fake_content = b"this is fake image content"
    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.return_value = []
        upload_response = client.post(
            "/documents/upload",
            files={"file": ("test-brief.png", fake_content, "image/png")},
            headers=parent_headers,
        )

    doc_id = upload_response.json()["id"]

    # Download the file
    response = client.get(f"/documents/{doc_id}/file", headers=parent_headers)
    assert response.status_code == 200
    assert response.content == fake_content
    assert response.headers["content-type"] == "image/png"
    assert "Content-Disposition" in response.headers


def test_get_document_file_wrong_family_rejected(client, db_session):
    """Cannot download a document from another family."""
    parent_headers_1 = _register_parent(client, email="parent1@example.ch")
    parent_headers_2 = _register_parent(client, email="parent2@example.ch")

    # Upload document in family 1
    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.return_value = []
        upload_response = client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers_1,
        )

    doc_id = upload_response.json()["id"]

    # Try to download as family 2
    response = client.get(f"/documents/{doc_id}/file", headers=parent_headers_2)
    assert response.status_code == 404


def test_resolve_event_with_child_user_id(client, db_session):
    """Resolve a pending event by assigning it to a child."""
    parent_headers = _register_parent(client, email="parent@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    child = _create_child(db_session, family_id, display_name="Bob")

    # Upload document with pending event
    # Note: extract_letter_events returns JSON-style strings for dates
    mocked_events = [
        {
            "title": "Schulausflug",
            "event_date": "2026-09-15",
            "event_time": "10:00",
            "person_hint": None,
        }
    ]

    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.return_value = mocked_events

        upload_response = client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers,
        )

    doc_id = upload_response.json()["id"]
    event_id = upload_response.json()["events"][0]["id"]

    # Resolve the event
    resolve_response = client.post(
        f"/documents/{doc_id}/events/{event_id}/resolve",
        json={"child_user_id": str(child.id)},
        headers=parent_headers,
    )

    assert resolve_response.status_code == 200
    resolved_event = resolve_response.json()
    assert resolved_event["status"] == "confirmed"
    assert resolved_event["resolved_child_user_id"] == str(child.id)
    assert resolved_event["calendar_event_id"] is not None

    # Verify document status changed to processed
    doc_response = client.get("/documents", headers=parent_headers)
    docs = doc_response.json()
    assert len(docs) == 1
    assert docs[0]["status"] == "processed"


def test_resolve_event_dismiss(client, db_session):
    """Dismiss a pending event (resolve with null child_user_id)."""
    parent_headers = _register_parent(client, email="parent@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    _create_child(db_session, family_id, display_name="Bob")

    # Upload document with pending event
    # Note: extract_letter_events returns JSON-style strings for dates
    mocked_events = [
        {
            "title": "Schulausflug",
            "event_date": "2026-09-15",
            "event_time": "10:00",
            "person_hint": None,
        }
    ]

    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.return_value = mocked_events

        upload_response = client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers,
        )

    doc_id = upload_response.json()["id"]
    event_id = upload_response.json()["events"][0]["id"]

    # Dismiss the event
    resolve_response = client.post(
        f"/documents/{doc_id}/events/{event_id}/resolve",
        json={"child_user_id": None},
        headers=parent_headers,
    )

    assert resolve_response.status_code == 200
    resolved_event = resolve_response.json()
    assert resolved_event["status"] == "dismissed"
    assert resolved_event["calendar_event_id"] is None


def test_resolve_event_with_wrong_family_child_rejected(client, db_session):
    """Cannot resolve an event with a child from another family."""
    parent_headers_1 = _register_parent(client, email="parent1@example.ch")
    parent_headers_2 = _register_parent(client, email="parent2@example.ch")

    # Get family IDs
    family1_response = client.get("/families/me", headers=parent_headers_1)
    _family1_id = uuid.UUID(family1_response.json()["id"])  # noqa: F841

    family2_response = client.get("/families/me", headers=parent_headers_2)
    family2_id = uuid.UUID(family2_response.json()["id"])

    # Create child in family 2
    child_in_family2 = _create_child(db_session, family2_id, display_name="Bob")

    # Upload document in family 1
    # Note: extract_letter_events returns JSON-style strings for dates
    mocked_events = [
        {
            "title": "Schulausflug",
            "event_date": "2026-09-15",
            "event_time": "10:00",
            "person_hint": None,
        }
    ]

    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.return_value = mocked_events

        upload_response = client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers_1,
        )

    doc_id = upload_response.json()["id"]
    event_id = upload_response.json()["events"][0]["id"]

    # Try to resolve with child from family 2
    resolve_response = client.post(
        f"/documents/{doc_id}/events/{event_id}/resolve",
        json={"child_user_id": str(child_in_family2.id)},
        headers=parent_headers_1,
    )

    assert resolve_response.status_code == 404


def test_upload_non_image_file_rejected(client):
    """Uploading a non-image file returns 400."""
    parent_headers = _register_parent(client, email="parent@example.ch")

    response = client.post(
        "/documents/upload",
        files={"file": ("document.txt", b"text content", "text/plain")},
        headers=parent_headers,
    )

    assert response.status_code == 400
    assert "Nur Bilddateien" in response.json()["detail"]


def test_upload_non_parent_rejected(client):
    """Non-parent users cannot upload documents."""
    response = client.post(
        "/documents/upload",
        files={"file": ("brief.png", b"fake image", "image/png")},
    )

    assert response.status_code == 401


def test_upload_with_vision_api_error_returns_503(client, db_session):
    """If Claude vision API fails, return 503."""
    parent_headers = _register_parent(client, email="parent@example.ch")

    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.side_effect = RuntimeError("Anthropic-API-Fehler beim Verarbeiten des Bildes.")

        response = client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers,
        )

    assert response.status_code == 503
    assert "Anthropic-API" in response.json()["detail"]


def test_upload_with_vision_parse_error_returns_400(client, db_session):
    """If Claude vision returns unparseable JSON, return 400."""
    parent_headers = _register_parent(client, email="parent@example.ch")

    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.side_effect = ValueError("Konnte Brief-Bild nicht auswerten.")

        response = client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers,
        )

    assert response.status_code == 400
    assert "Konnte Brief-Bild nicht auswerten" in response.json()["detail"]


def test_resolve_document_event_wrong_family_rejected(client, db_session):
    """Cannot resolve an event from another family's document."""
    parent_headers_1 = _register_parent(client, email="parent1@example.ch")
    parent_headers_2 = _register_parent(client, email="parent2@example.ch")

    response = client.get("/families/me", headers=parent_headers_1)
    family1_id = uuid.UUID(response.json()["id"])

    # Create a child so there's a user record (even though we don't use it)
    _create_child(db_session, family1_id, display_name="Bob")

    # Upload document in family 1
    # Note: extract_letter_events returns JSON-style strings for dates
    mocked_events = [
        {
            "title": "Schulausflug",
            "event_date": "2026-09-15",
            "event_time": "10:00",
            "person_hint": None,
        }
    ]

    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.return_value = mocked_events

        upload_response = client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers_1,
        )

    doc_id = upload_response.json()["id"]
    event_id = upload_response.json()["events"][0]["id"]

    # Try to resolve as family 2
    resolve_response = client.post(
        f"/documents/{doc_id}/events/{event_id}/resolve",
        json={"child_user_id": None},
        headers=parent_headers_2,
    )

    assert resolve_response.status_code == 404


def test_resolve_child_only_rejected(client, db_session):
    """Child users cannot resolve document events."""
    parent_headers = _register_parent(client, email="parent@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])  # noqa: F841

    child = _create_child(db_session, family_id, display_name="Bob")

    # Upload document
    # Note: extract_letter_events returns JSON-style strings for dates
    mocked_events = [
        {
            "title": "Schulausflug",
            "event_date": "2026-09-15",
            "event_time": "10:00",
            "person_hint": None,
        }
    ]

    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.return_value = mocked_events

        upload_response = client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers,
        )

    doc_id = upload_response.json()["id"]
    event_id = upload_response.json()["events"][0]["id"]

    # Login as child
    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(family_id), "user_id": str(child.id), "pin": "1234"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}

    # Try to resolve as child
    resolve_response = client.post(
        f"/documents/{doc_id}/events/{event_id}/resolve",
        json={"child_user_id": None},
        headers=child_headers,
    )

    assert resolve_response.status_code == 403


def test_upload_document_with_malformed_event_date(client, db_session):
    """Upload document where extracted event has malformed date is created as pending_clarification."""
    parent_headers = _register_parent(client, email="parent@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    # Create a child with matching hint
    child = _create_child(db_session, family_id, display_name="Anna")

    # Mock extraction returning an event with a malformed date and matching person_hint
    # Even though person_hint matches uniquely, the malformed date should prevent auto-confirm
    mocked_events = [
        {
            "title": "Schulausflug",
            "event_date": "not-a-date",  # Malformed date
            "event_time": "10:00",
            "person_hint": "Anna",
        },
        {
            "title": "Elternabend",
            "event_date": "2026-09-20",  # Valid date
            "event_time": None,
            "person_hint": "Anna",
        },
    ]

    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.return_value = mocked_events

        response = client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers,
        )

    # Upload should succeed even with malformed date
    assert response.status_code == 201
    doc = response.json()
    assert len(doc["events"]) == 2

    # First event: malformed date, should be pending_clarification even though hint matches
    assert doc["events"][0]["title"] == "Schulausflug"
    assert doc["events"][0]["status"] == "pending_clarification"
    assert doc["events"][0]["resolved_child_user_id"] is None
    assert doc["events"][0]["calendar_event_id"] is None

    # Second event: valid date and matching hint, should be confirmed
    assert doc["events"][1]["title"] == "Elternabend"
    assert doc["events"][1]["status"] == "confirmed"
    assert doc["events"][1]["resolved_child_user_id"] == str(child.id)
    assert doc["events"][1]["calendar_event_id"] is not None

    # Document status should be needs_clarification
    assert doc["status"] == "needs_clarification"


def test_upload_document_with_malformed_event_time(client, db_session):
    """Upload document where extracted event has malformed time is created as pending_clarification."""
    parent_headers = _register_parent(client, email="parent@example.ch")

    response = client.get("/families/me", headers=parent_headers)
    family_id = uuid.UUID(response.json()["id"])

    # Create a child with matching hint
    _create_child(db_session, family_id, display_name="Bob")

    # Mock extraction returning an event with a malformed time and matching person_hint
    mocked_events = [
        {
            "title": "Schulfeier",
            "event_date": "2026-09-15",
            "event_time": "25:99",  # Malformed time
            "person_hint": "Bob",
        }
    ]

    with patch("app.api.documents.extract_letter_events") as mock_extract:
        mock_extract.return_value = mocked_events

        response = client.post(
            "/documents/upload",
            files={"file": ("brief.png", b"fake image", "image/png")},
            headers=parent_headers,
        )

    # Upload should succeed even with malformed time
    assert response.status_code == 201
    doc = response.json()
    assert len(doc["events"]) == 1

    # Event: malformed time, should be pending_clarification even though hint matches
    assert doc["events"][0]["title"] == "Schulfeier"
    assert doc["events"][0]["status"] == "pending_clarification"
    assert doc["events"][0]["resolved_child_user_id"] is None
    assert doc["events"][0]["calendar_event_id"] is None

    # Document status should be needs_clarification
    assert doc["status"] == "needs_clarification"
