"""Tests for recurring calendar events and multi-person assignment."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.calendar_recurrence import (
    DAILY_WINDOW_DAYS,
    WEEKLY_WINDOW_DAYS,
    generate_occurrences_for_series,
)
from app.core.security import hash_secret
from app.models.calendar import CalendarEvent
from app.models.family import Family
from app.models.user import User, UserRole


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


def _create_child(db_session, family_id, name="Kind", pin="1234"):
    child = User(
        family_id=family_id,
        role=UserRole.CHILD,
        display_name=name,
        pin_hash=hash_secret(pin),
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    return child


def _midnight_utc_today() -> datetime:
    """Anchor recurring test events at today's midnight UTC.

    The create endpoint computes its own "today" as the real wall-clock time when
    the test runs, with no way to inject a fixed value. Anchoring the event's own
    start at 00:00 UTC (always <= "now"'s time-of-day) keeps the rolling-window
    occurrence count exact regardless of what time of day the test actually runs.
    """
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def test_daily_recurring_event_creates_rolling_window(client, db_session):
    """A daily recurring event generates the whole rolling window of occurrences."""
    parent_headers = _register_parent(client, email="daily@example.ch")
    start = _midnight_utc_today()

    response = client.post(
        "/calendar/events",
        headers=parent_headers,
        json={
            "title": "Medikament",
            "start": _iso(start),
            "end": _iso(start + timedelta(minutes=30)),
            "recurrence_rule": "daily",
        },
    )

    assert response.status_code == 201
    root = response.json()
    assert root["recurrence_rule"] == "daily"
    assert root["series_id"] is None

    all_in_series = db_session.scalars(
        select(CalendarEvent).where(CalendarEvent.title == "Medikament")
    ).all()
    assert len(all_in_series) == DAILY_WINDOW_DAYS + 1


def test_weekly_recurring_event_creates_weekly_spaced_occurrences(client, db_session):
    """A weekly recurring event generates occurrences 7 days apart."""
    parent_headers = _register_parent(client, email="weekly@example.ch")
    start = _midnight_utc_today()

    response = client.post(
        "/calendar/events",
        headers=parent_headers,
        json={
            "title": "Klavierstunde",
            "start": _iso(start),
            "end": _iso(start + timedelta(hours=1)),
            "recurrence_rule": "weekly",
        },
    )

    assert response.status_code == 201

    events = db_session.scalars(
        select(CalendarEvent).where(CalendarEvent.title == "Klavierstunde")
    ).all()
    expected_count = WEEKLY_WINDOW_DAYS // 7 + 1
    assert len(events) == expected_count

    starts = sorted(e.start for e in events)
    for a, b in zip(starts, starts[1:], strict=False):
        assert (b - a).days == 7


def test_event_without_recurrence_creates_single_occurrence(client, db_session):
    """Omitting recurrence_rule keeps the original one-off behavior."""
    parent_headers = _register_parent(client, email="once@example.ch")
    start = _midnight_utc_today()

    response = client.post(
        "/calendar/events",
        headers=parent_headers,
        json={"title": "Zahnarzt", "start": _iso(start), "end": _iso(start + timedelta(hours=1))},
    )

    assert response.status_code == 201
    assert response.json()["recurrence_rule"] is None
    assert response.json()["series_id"] is None

    events = db_session.scalars(select(CalendarEvent).where(CalendarEvent.title == "Zahnarzt")).all()
    assert len(events) == 1


def test_owner_user_ids_creates_one_event_per_person(client, db_session):
    """Assigning to multiple people creates one event per person."""
    parent_headers = _register_parent(client, email="multi@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "multi@example.ch"))
    child_a = _create_child(db_session, admin.family_id, name="Lena")
    child_b = _create_child(db_session, admin.family_id, name="Mia")
    start = _midnight_utc_today()

    response = client.post(
        "/calendar/events",
        headers=parent_headers,
        json={
            "title": "Fussballtraining",
            "start": _iso(start),
            "end": _iso(start + timedelta(hours=1, minutes=30)),
            "owner_user_ids": [str(child_a.id), str(child_b.id)],
        },
    )

    assert response.status_code == 201

    events = db_session.scalars(
        select(CalendarEvent).where(CalendarEvent.title == "Fussballtraining")
    ).all()
    assert len(events) == 2
    owners = {e.owner_user_id for e in events}
    assert owners == {child_a.id, child_b.id}


def test_owner_user_ids_with_recurrence_generates_independent_series_per_person(client, db_session):
    """Multi-person + recurring creates a separate rolling window for each person."""
    parent_headers = _register_parent(client, email="multidaily@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "multidaily@example.ch"))
    child_a = _create_child(db_session, admin.family_id, name="Lena")
    child_b = _create_child(db_session, admin.family_id, name="Mia")
    start = _midnight_utc_today()

    response = client.post(
        "/calendar/events",
        headers=parent_headers,
        json={
            "title": "Zahnspange putzen",
            "start": _iso(start),
            "end": _iso(start + timedelta(minutes=10)),
            "owner_user_ids": [str(child_a.id), str(child_b.id)],
            "recurrence_rule": "daily",
        },
    )

    assert response.status_code == 201

    events_a = db_session.scalars(
        select(CalendarEvent).where(
            CalendarEvent.title == "Zahnspange putzen", CalendarEvent.owner_user_id == child_a.id
        )
    ).all()
    events_b = db_session.scalars(
        select(CalendarEvent).where(
            CalendarEvent.title == "Zahnspange putzen", CalendarEvent.owner_user_id == child_b.id
        )
    ).all()
    assert len(events_a) == DAILY_WINDOW_DAYS + 1
    assert len(events_b) == DAILY_WINDOW_DAYS + 1


def test_owner_user_ids_rejects_member_outside_family(client, db_session):
    """A parent cannot assign an event to someone outside their own family."""
    _register_parent(client, email="outsideA@example.ch")
    _register_parent(client, email="outsideB@example.ch")
    other_admin = db_session.scalar(select(User).where(User.email == "outsideB@example.ch"))
    other_child = _create_child(db_session, other_admin.family_id)
    start = _midnight_utc_today()

    login_response = client.post(
        "/auth/login", json={"email": "outsideA@example.ch", "password": "s3hr-sicher"}
    )
    parent_headers = {"Authorization": f"Bearer {login_response.json()['access_token']}"}

    response = client.post(
        "/calendar/events",
        headers=parent_headers,
        json={
            "title": "Termin",
            "start": _iso(start),
            "end": _iso(start + timedelta(hours=1)),
            "owner_user_ids": [str(other_child.id)],
        },
    )

    assert response.status_code == 404


def test_recurrence_rule_rejects_invalid_value(client, db_session):
    """Only 'daily' and 'weekly' are accepted recurrence rules."""
    parent_headers = _register_parent(client, email="invalidrule@example.ch")
    start = _midnight_utc_today()

    response = client.post(
        "/calendar/events",
        headers=parent_headers,
        json={
            "title": "Termin",
            "start": _iso(start),
            "end": _iso(start + timedelta(hours=1)),
            "recurrence_rule": "monthly",
        },
    )

    assert response.status_code == 422


def test_generate_occurrences_for_series_tops_up_without_duplicating(db_session):
    """Calling the generator again only adds newly-due occurrences, never duplicates."""
    family = Family(name="Topup-Familie")
    db_session.add(family)
    db_session.commit()
    db_session.refresh(family)

    start = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    root = CalendarEvent(
        family_id=family.id,
        title="Test",
        start=start,
        end=start + timedelta(minutes=30),
        recurrence_rule="daily",
        source="native",
    )
    db_session.add(root)
    db_session.commit()
    db_session.refresh(root)

    first_batch = generate_occurrences_for_series(db_session, root, today=start)
    assert len(first_batch) == DAILY_WINDOW_DAYS  # root itself is occurrence 0, not re-created

    second_batch = generate_occurrences_for_series(db_session, root, today=start)
    assert len(second_batch) == 0

    all_events = db_session.scalars(
        select(CalendarEvent).where(CalendarEvent.series_id == root.id)
    ).all()
    assert len(all_events) == DAILY_WINDOW_DAYS

    starts = sorted(e.start for e in all_events)
    assert starts[0] - root.start == timedelta(days=1)
