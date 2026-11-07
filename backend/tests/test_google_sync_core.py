"""Core Google -> family calendar import (app.core.google_sync): tag parsing, member mapping, cleanup, time
handling, paging, error handling, credentials. No network: the Google service is a fake."""

import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from google.auth.exceptions import RefreshError
from sqlalchemy import select

from app.core import google_sync
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.google_sync import Member, NeedsReauth, parse_title
from app.models.calendar import CalendarEvent, GoogleCalendarSelection
from tests.google_helpers import (
    FakeGoogle,
    all_day,
    http_error,
    make_account,
    make_family,
    soon,
    sync,
    timed,
    valid_creds_json,
)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _m(name, *, parent=False, email=None):
    return Member(id=uuid.uuid4(), name=name, email=email, is_parent=parent)


MIA, LEO, PAPA, MAMA = _m("Mia"), _m("Leo"), _m("Papa", parent=True, email="papa@example.ch"), _m("Mama", parent=True)
ANNA_MARIA = _m("Anna Maria")
SAM_1, SAM_2 = _m("Sam"), _m("Sam")
MEMBERS = [MIA, LEO, PAPA, MAMA, ANNA_MARIA, SAM_1, SAM_2]


# ------------------------------------------------------------------------------------------ tag parsing


@pytest.mark.parametrize(
    ("summary", "title", "owners"),
    [
        ("Mia: Zahnarzt", "Zahnarzt", [MIA]),
        ("mia: Zahnarzt", "Zahnarzt", [MIA]),
        ("MIA:Zahnarzt", "Zahnarzt", [MIA]),
        ("Mia, Leo: Schwimmen", "Schwimmen", [MIA, LEO]),
        ("Mia und Leo: Schwimmen", "Schwimmen", [MIA, LEO]),
        ("Mia, Leo und Papa: Essen", "Essen", [MIA, LEO, PAPA]),
        ("Zahnarzt #Mia", "Zahnarzt", [MIA]),
        ("Zahnarzt @leo um 10", "Zahnarzt um 10", [LEO]),
        ("#Mia #Leo Turnen", "Turnen", [MIA, LEO]),
        ("Turnen @MIA", "Turnen", [MIA]),
        ("Mia: Turnen #Leo", "Turnen", [MIA, LEO]),
        ("Mia: Turnen #Mia", "Turnen", [MIA]),
        ("Anna Maria: Ballett", "Ballett", [ANNA_MARIA]),
        ("Ballett #Anna Maria", "Ballett", [ANNA_MARIA]),
        ("Mia, Unbekannt: Foo", "Foo", [MIA]),  # unknown names are ignored, the tag is still stripped
        # no tag
        ("Zahnarzt", "Zahnarzt", []),
        ("Termin: Zahnarzt", "Termin: Zahnarzt", []),  # a normal colon title stays untouched
        ("Zahnarzt #Unbekannt", "Zahnarzt #Unbekannt", []),
        ("Zahnarzt #Miax", "Zahnarzt #Miax", []),  # no partial matches
        ("Zahnarzt #Mi", "Zahnarzt #Mi", []),
        ("Miax: Foo", "Miax: Foo", []),
        ("Mi: Foo", "Mi: Foo", []),
        ("Sam: Fussball", "Sam: Fussball", []),  # two members called Sam: ambiguous, ignored
        ("Fussball #Sam", "Fussball #Sam", []),
        ("Mia:", "Mia:", []),  # nothing after the tag
        ("Mail info@Mia.ch senden", "Mail info@Mia.ch senden", []),  # '@' inside a word is no tag
        ("", "", []),
    ],
)
def test_parse_title_table(summary, title, owners):
    parsed_title, parsed_owners = parse_title(summary, MEMBERS)
    assert parsed_title == title
    assert parsed_owners == [m.id for m in owners]


def test_sanitize_title_limits_length_and_never_returns_empty():
    assert google_sync.sanitize_title("x" * 500) == "x" * 200
    assert google_sync.sanitize_title("  Zahn\narzt\t ") == "Zahn arzt"
    assert google_sync.sanitize_title("") == "(Ohne Titel)"
    assert google_sync.sanitize_title(None) == "(Ohne Titel)"
    assert google_sync.sanitize_title("\x00\x07") == "(Ohne Titel)"


# ------------------------------------------------------------------------------------ member mapping


def _rows(db, family, **filters):
    stmt = select(CalendarEvent).where(CalendarEvent.family_id == family.id, CalendarEvent.source == "google")
    return db.scalars(stmt.order_by(CalendarEvent.title)).all()


def _by_title(rows):
    return {(r.title, r.owner_user_id) for r in rows}


def test_tag_in_title_assigns_members_and_is_stripped(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    fake = FakeGoogle()
    fake.set_events("primary", [timed("e1", "Mia, Leo: Schwimmen", soon()), timed("e2", "Zahnarzt #Mia", soon(30))])

    assert sync(db_session, account, fake) == "ok"

    rows = _rows(db_session, f["family"])
    assert _by_title(rows) == {
        ("Schwimmen", f["mia"].id),
        ("Schwimmen", f["leo"].id),
        ("Zahnarzt", f["mia"].id),
    }
    assert all(r.sync_account_id == account.id and r.source_calendar_id == "primary" for r in rows)


def test_attendee_email_of_a_parent_assigns_that_parent(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    fake = FakeGoogle()
    fake.set_events(
        "primary",
        [
            timed("e1", "Elternabend", soon(), attendees=[{"email": "Mama@Example.ch"}, {"email": "fremd@x.ch"}]),
            timed("e2", "Nur fremde", soon(30), attendees=[{"email": "fremd@x.ch"}]),
        ],
    )

    sync(db_session, account, fake)

    assert _by_title(_rows(db_session, f["family"])) == {("Elternabend", f["mama"].id), ("Nur fremde", None)}


def test_calendar_default_owners_apply_when_nothing_else_does(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"], owners={"primary": [f["mia"].id, f["leo"].id]})
    fake = FakeGoogle()
    fake.set_events("primary", [timed("e1", "Schule", soon())])

    sync(db_session, account, fake)

    assert _by_title(_rows(db_session, f["family"])) == {("Schule", f["mia"].id), ("Schule", f["leo"].id)}


def test_priority_tag_over_attendees_over_calendar_default(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"], owners={"primary": [f["leo"].id]})
    fake = FakeGoogle()
    fake.set_events(
        "primary",
        [
            timed("tag", "Mia: A", soon(), attendees=[{"email": "mama@example.ch"}]),
            timed("att", "B", soon(30), attendees=[{"email": "mama@example.ch"}]),
            timed("def", "C", soon(40)),
        ],
    )

    sync(db_session, account, fake)

    assert _by_title(_rows(db_session, f["family"])) == {
        ("A", f["mia"].id),
        ("B", f["mama"].id),
        ("C", f["leo"].id),
    }


def test_no_owner_means_whole_family_single_row(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    fake = FakeGoogle()
    fake.set_events("primary", [timed("e1", "Familienfest", soon())])

    sync(db_session, account, fake)

    rows = _rows(db_session, f["family"])
    assert len(rows) == 1 and rows[0].owner_user_id is None


def test_stale_default_owner_and_dashboard_tag_are_ignored(db_session):
    f = make_family(db_session)
    ghost = uuid.uuid4()
    account = make_account(db_session, f["papa"], owners={"primary": [ghost]})
    fake = FakeGoogle()
    fake.set_events("primary", [timed("e1", "Wand: Foo", soon())])

    sync(db_session, account, fake)

    rows = _rows(db_session, f["family"])
    assert [(r.title, r.owner_user_id) for r in rows] == [("Wand: Foo", None)]  # wall display is not a member


def test_changing_the_tag_updates_rows_and_removes_rows_that_no_longer_apply(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    fake = FakeGoogle()
    start = soon()
    fake.set_events("primary", [timed("e1", "Mia, Leo: Turnen", start)])
    sync(db_session, account, fake)
    assert len(_rows(db_session, f["family"])) == 2

    fake.set_events("primary", [timed("e1", "Leo: Turnen am Abend", start)])
    sync(db_session, account, fake)

    rows = _rows(db_session, f["family"])
    assert _by_title(rows) == {("Turnen am Abend", f["leo"].id)}


def test_resync_is_idempotent_and_updates_in_place(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    fake = FakeGoogle()
    fake.set_events("primary", [timed("e1", "Alt", soon())])
    sync(db_session, account, fake)
    first_id = _rows(db_session, f["family"])[0].id

    fake.set_events("primary", [timed("e1", "Neu", soon(72))])
    sync(db_session, account, fake)

    rows = _rows(db_session, f["family"])
    assert len(rows) == 1 and rows[0].id == first_id and rows[0].title == "Neu"


def test_private_and_confidential_events_are_flagged(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    fake = FakeGoogle()
    fake.set_events(
        "primary",
        [
            timed("a", "Privat", soon(), visibility="private"),
            timed("b", "Vertraulich", soon(30), visibility="confidential"),
            timed("c", "Oeffentlich", soon(40), visibility="public"),
            timed("d", "Standard", soon(50), visibility="default"),
            timed("e", "Ohne", soon(60)),
        ],
    )

    sync(db_session, account, fake)

    flags = {r.title: r.is_private for r in _rows(db_session, f["family"])}
    assert flags == {"Privat": True, "Vertraulich": True, "Oeffentlich": False, "Standard": False, "Ohne": False}


# --------------------------------------------------------------------------------------------- cleanup


def test_vanished_cancelled_and_out_of_window_events_are_deleted(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    fake = FakeGoogle()
    fake.set_events(
        "primary",
        [timed("keep", "Bleibt", soon()), timed("gone", "Weg", soon(30)), timed("cancel", "Abgesagt", soon(40)),
         timed("moved", "Verschoben", soon(50))],
    )
    sync(db_session, account, fake)
    assert len(_rows(db_session, f["family"])) == 4

    # "gone" vanished, "cancel" is returned as cancelled, "moved" is no longer in the window (not returned)
    fake.set_events("primary", [timed("keep", "Bleibt", soon()), {"id": "cancel", "status": "cancelled"}])
    sync(db_session, account, fake)

    assert [r.title for r in _rows(db_session, f["family"])] == ["Bleibt"]


def test_cleanup_only_touches_rows_of_this_account_and_calendar(db_session):
    f = make_family(db_session)
    other_family = make_family(db_session, name="Andere", tag="2")
    account = make_account(db_session, f["papa"], selections=("cal-a", "cal-b"))
    other_account = make_account(db_session, f["mama"], selections=("cal-a",))
    foreign_account = make_account(db_session, other_family["papa"], selections=("cal-a",))
    fake = FakeGoogle()
    fake.set_events("cal-a", [timed("a1", "A1", soon())])
    fake.set_events("cal-b", [timed("b1", "B1", soon(30))])
    sync(db_session, account, fake)
    other_fake = FakeGoogle()
    other_fake.set_events("cal-a", [timed("x1", "Mamas", soon(40))])
    sync(db_session, other_account, other_fake)
    foreign_fake = FakeGoogle()
    foreign_fake.set_events("cal-a", [timed("f1", "Fremde Familie", soon())])
    sync(db_session, foreign_account, foreign_fake)
    native = CalendarEvent(family_id=f["family"].id, title="Nativ", start=soon(), end=soon(2), source="native")
    caldav = CalendarEvent(
        family_id=f["family"].id, title="CalDAV", start=soon(), end=soon(2), source="caldav", external_id="a1"
    )
    db_session.add_all([native, caldav])
    db_session.commit()

    # cal-a is now empty for the first account
    fake.set_events("cal-a", [])
    sync(db_session, account, fake)

    titles = {r.title for r in db_session.scalars(select(CalendarEvent))}
    assert "A1" not in titles
    assert {"B1", "Mamas", "Fremde Familie", "Nativ", "CalDAV"} <= titles


def test_deselected_calendars_events_are_removed_on_sync(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"], selections=("cal-a", "cal-b"))
    fake = FakeGoogle()
    fake.set_events("cal-a", [timed("a1", "A1", soon())])
    fake.set_events("cal-b", [timed("b1", "B1", soon(30))])
    sync(db_session, account, fake)

    db_session.delete(db_session.scalar(select(GoogleCalendarSelection).where(
        GoogleCalendarSelection.google_calendar_id == "cal-b")))
    db_session.commit()
    sync(db_session, account, fake)

    assert [r.title for r in _rows(db_session, f["family"])] == ["A1"]


def test_failed_calendar_keeps_its_events_and_does_not_stop_the_others(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"], selections=("cal-a", "cal-b"))
    fake = FakeGoogle()
    fake.set_events("cal-a", [timed("a1", "A1", soon())])
    fake.set_events("cal-b", [timed("b1", "B1", soon(30))])
    sync(db_session, account, fake)

    fake.errors["cal-a"] = http_error(500)
    fake.set_events("cal-b", [timed("b1", "B1 neu", soon(30)), timed("b2", "B2", soon(40))])
    status = sync(db_session, account, fake)

    assert status == "error"
    db_session.refresh(account)
    assert account.status == "error" and "Kalender cal-a" in account.last_error
    assert {r.title for r in _rows(db_session, f["family"])} == {"A1", "B1 neu", "B2"}  # A1 not deleted on error


def test_success_sets_ok_clears_error_and_stamps_last_synced(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"], status="error", last_error="Alt", synced=False)
    fake = FakeGoogle()
    fake.set_events("primary", [timed("e1", "X", soon())])

    sync(db_session, account, fake, now=NOW)

    db_session.refresh(account)
    assert (account.status, account.last_error, account.last_synced_at) == ("ok", None, NOW)


# ---------------------------------------------------------------------------------- time handling


def test_all_day_event_is_anchored_at_zurich_midnight_with_exclusive_end(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    fake = FakeGoogle()
    day = (datetime.now(UTC) + timedelta(days=10)).date()
    fake.set_events("primary", [all_day("e1", "Feiertag", day, day + timedelta(days=1))])

    sync(db_session, account, fake)

    row = _rows(db_session, f["family"])[0]
    zurich = ZoneInfo("Europe/Zurich")
    assert row.all_day is True
    assert row.start.tzinfo is not None and row.end.tzinfo is not None
    assert row.start.astimezone(zurich) == datetime(day.year, day.month, day.day, tzinfo=zurich)
    assert row.end - row.start == timedelta(days=1)  # same shape as parent-letter all-day rows (start + 1 day)


@pytest.mark.parametrize(
    ("first", "end", "start_utc", "end_utc"),
    [
        # summer time (UTC+2) and winter time (UTC+1), multi-day event, DST change inside the range
        (date(2026, 7, 10), date(2026, 7, 11), datetime(2026, 7, 9, 22, tzinfo=UTC),
         datetime(2026, 7, 10, 22, tzinfo=UTC)),
        (date(2026, 12, 24), date(2026, 12, 27), datetime(2026, 12, 23, 23, tzinfo=UTC),
         datetime(2026, 12, 26, 23, tzinfo=UTC)),
        (date(2026, 10, 24), date(2026, 10, 27), datetime(2026, 10, 23, 22, tzinfo=UTC),
         datetime(2026, 10, 26, 23, tzinfo=UTC)),
    ],
)
def test_all_day_times_across_dst(first, end, start_utc, end_utc):
    start, stop, is_all_day = google_sync.event_times(all_day("e", "x", first, end), ZoneInfo("Europe/Zurich"))
    assert is_all_day and start == start_utc and stop == end_utc


def test_all_day_without_valid_end_defaults_to_one_day():
    tz = ZoneInfo("Europe/Zurich")
    event = {"id": "e", "start": {"date": "2026-10-10"}, "end": {"date": "2026-10-10"}}
    start, end, _ = google_sync.event_times(event, tz)
    assert end - start == timedelta(days=1)
    event = {"id": "e", "start": {"date": "2026-10-10"}}
    assert google_sync.event_times(event, tz)[1] - start == timedelta(days=1)


def test_timed_events_keep_their_instant_and_naive_values_use_the_given_zone():
    tz = ZoneInfo("Europe/Zurich")
    event = {"start": {"dateTime": "2026-10-10T10:00:00+02:00"}, "end": {"dateTime": "2026-10-10T11:30:00Z"}}
    start, end, is_all_day = google_sync.event_times(event, tz)
    assert not is_all_day
    assert start == datetime(2026, 10, 10, 8, 0, tzinfo=UTC) and end == datetime(2026, 10, 10, 11, 30, tzinfo=UTC)
    assert start.tzinfo is not None and end.tzinfo is not None

    naive = {
        "start": {"dateTime": "2026-10-10T10:00:00", "timeZone": "America/New_York"},
        "end": {"dateTime": "2026-10-10T11:00:00", "timeZone": "America/New_York"},
    }
    start, end, _ = google_sync.event_times(naive, tz)
    assert start == datetime(2026, 10, 10, 14, 0, tzinfo=UTC)
    # no zone at all: the app timezone
    start, _, _ = google_sync.event_times({"start": {"dateTime": "2026-10-10T10:00:00"}}, tz)
    assert start == datetime(2026, 10, 10, 8, 0, tzinfo=UTC)
    assert end - start != timedelta(0)


def test_event_without_usable_start_is_skipped(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    fake = FakeGoogle()
    fake.set_events(
        "primary",
        [{"id": "bad", "summary": "Kaputt", "start": {"dateTime": "nicht-ein-datum"}}, {"summary": "Ohne id"},
         timed("ok", "Gut", soon())],
    )

    assert sync(db_session, account, fake) == "ok"
    assert [r.title for r in _rows(db_session, f["family"])] == ["Gut"]


def test_app_timezone_setting_controls_the_all_day_anchor(monkeypatch):
    settings = google_sync.get_settings()
    monkeypatch.setattr(google_sync, "get_settings", lambda: settings.model_copy(update={"app_timezone": "Asia/Tokyo"}))
    assert str(google_sync.get_timezone()) == "Asia/Tokyo"
    monkeypatch.setattr(google_sync, "get_settings", lambda: settings.model_copy(update={"app_timezone": "Nope/Nope"}))
    assert str(google_sync.get_timezone()) == "Europe/Zurich"
    assert settings.app_timezone == "Europe/Zurich"


# ----------------------------------------------------------------------------------------------- paging


def test_paging_reads_all_three_pages_with_the_expected_window(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    fake = FakeGoogle()
    fake.set_events("primary", [timed(f"e{i}", f"Termin {i}", soon(i + 1)) for i in range(9)], pages=3)

    sync(db_session, account, fake, now=NOW)

    assert len(_rows(db_session, f["family"])) == 9
    assert [c.get("pageToken") for c in fake.event_calls] == [None, "p1", "p2"]
    first = fake.event_calls[0]
    assert first["calendarId"] == "primary"
    assert first["timeMin"] == "2026-09-27T12:00:00Z"  # now - 7 days
    assert first["timeMax"] == "2027-01-02T12:00:00Z"  # now + 90 days
    assert first["singleEvents"] is True and first["showDeleted"] is False


def test_page_cap_imports_what_was_read_but_never_deletes_and_reports_an_error(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    old = CalendarEvent(
        family_id=f["family"].id, title="Alt", start=soon(), end=soon(2), source="google", external_id="old",
        source_calendar_id="primary", sync_account_id=account.id,
    )
    db_session.add(old)
    db_session.commit()
    fake = FakeGoogle()
    fake.set_events("primary", [timed(f"e{i}", f"T{i}", soon(i + 1)) for i in range(25)], pages=25)

    status = sync(db_session, account, fake)

    assert status == "error"
    assert len(fake.event_calls) == google_sync.MAX_PAGES
    db_session.refresh(account)
    assert account.last_error == google_sync.MSG_TRUNCATED
    titles = {r.title for r in _rows(db_session, f["family"])}
    assert "Alt" in titles and len(titles) == google_sync.MAX_PAGES + 1


# --------------------------------------------------------------------------------- errors and status


def test_revoked_refresh_token_marks_needs_reauth_and_keeps_events(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    fake = FakeGoogle()
    fake.set_events("primary", [timed("e1", "Bleibt", soon())])
    sync(db_session, account, fake)

    def revoked(_account, _db):
        raise NeedsReauth("refresh token rejected")

    assert sync(db_session, account, fake, credentials_factory=revoked) == "needs_reauth"

    db_session.refresh(account)
    assert account.status == "needs_reauth"
    assert account.last_error == google_sync.MSG_NEEDS_REAUTH and "neu verbinden" in account.last_error.lower()
    assert [r.title for r in _rows(db_session, f["family"])] == ["Bleibt"]


def test_unauthorized_response_from_google_marks_needs_reauth(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    fake = FakeGoogle()
    fake.errors["primary"] = http_error(401)

    assert sync(db_session, account, fake) == "needs_reauth"


def test_invalid_grant_during_refresh_marks_needs_reauth(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    account.oauth_token_encrypted = encrypt_secret(
        valid_creds_json(expiry=(datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None).isoformat())
    )
    db_session.commit()
    error = RefreshError("invalid_grant: Token has been expired or revoked.", {"error": "invalid_grant"})

    with patch("google.oauth2.credentials.Credentials.refresh", side_effect=error):
        status = google_sync.sync_account(db_session, account, service_factory=lambda _c: FakeGoogle())

    assert status == "needs_reauth"


def test_unexpected_failure_sets_error_without_leaking_details(db_session, caplog):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])

    def broken_service(_creds):
        raise RuntimeError("Bearer ya29.SECRET-TOKEN-123 refused")

    caplog.set_level(logging.DEBUG)
    status = google_sync.sync_account(
        db_session, account, credentials_factory=lambda _a, _d: object(), service_factory=broken_service
    )

    assert status == "error"
    db_session.refresh(account)
    assert account.last_error == google_sync.MSG_GENERIC_ERROR
    assert "SECRET-TOKEN" not in account.last_error
    assert "SECRET-TOKEN" not in caplog.text


def test_unreadable_credentials_need_reauth(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    account.oauth_token_encrypted = "kein-gueltiges-fernet-token"
    db_session.commit()

    assert google_sync.sync_account(db_session, account, service_factory=lambda _c: FakeGoogle()) == "needs_reauth"


def test_default_selection_for_a_never_synced_account_uses_the_primary_calendar(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"], selections=(), synced=False)
    account.google_email = None
    db_session.commit()
    fake = FakeGoogle(calendars=[
        {"id": "family@group.calendar.google.com", "summary": "Familie"},
        {"id": "mama@gmail.com", "summary": "Mama Privat", "primary": True},
    ])
    fake.set_events("mama@gmail.com", [timed("e1", "Test", soon())])

    sync(db_session, account, fake)

    selections = db_session.scalars(
        select(GoogleCalendarSelection).where(GoogleCalendarSelection.account_id == account.id)
    ).all()
    assert [(s.google_calendar_id, s.name, list(s.owner_user_ids)) for s in selections] == [
        ("mama@gmail.com", "Mama Privat", [])
    ]
    db_session.refresh(account)
    assert account.google_email == "mama@gmail.com"
    assert [r.source_calendar_id for r in _rows(db_session, f["family"])] == ["mama@gmail.com"]


def test_deselecting_everything_on_a_synced_account_syncs_nothing(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"], selections=())
    fake = FakeGoogle()

    assert sync(db_session, account, fake) == "ok"
    assert fake.event_calls == []
    assert db_session.scalars(select(GoogleCalendarSelection)).all() == []


def test_rows_of_another_account_are_not_taken_over_and_kept_rows_are_adopted(db_session):
    f = make_family(db_session)
    papa_account = make_account(db_session, f["papa"])
    mama_account = make_account(db_session, f["mama"])
    shared = FakeGoogle()
    shared.set_events("primary", [timed("same", "Gemeinsam", soon())])

    sync(db_session, papa_account, shared)
    sync(db_session, mama_account, shared)
    rows = _rows(db_session, f["family"])
    assert len(rows) == 1 and rows[0].sync_account_id == papa_account.id  # no duplicate, no tug of war

    rows[0].sync_account_id = None  # papa disconnected and kept the events
    db_session.commit()
    sync(db_session, mama_account, shared)
    rows = _rows(db_session, f["family"])
    assert len(rows) == 1 and rows[0].sync_account_id == mama_account.id


# ----------------------------------------------------------------------------------- credentials


def test_credentials_roundtrip_keeps_the_expiry():
    expiry = datetime(2026, 10, 4, 13, 0)
    creds = google_sync.credentials_from_dict(json.loads(valid_creds_json(expiry=expiry.isoformat())))
    assert creds.expiry == expiry
    data = google_sync.credentials_to_dict(creds)
    assert data["expiry"] == expiry.isoformat() and data["refresh_token"] == "refresh-token-xyz"
    assert google_sync.credentials_from_dict(data).expiry == expiry


def test_credentials_to_dict_tolerates_missing_expiry():
    creds = MagicMock(token="t", refresh_token="r", token_uri="u", client_id="c", client_secret="s",
                      scopes=["a"], expiry=MagicMock())
    assert google_sync.credentials_to_dict(creds)["expiry"] is None


def test_load_credentials_refreshes_an_expired_token_and_persists_the_new_expiry(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    account.oauth_token_encrypted = encrypt_secret(
        valid_creds_json(expiry=(datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None).isoformat())
    )
    db_session.commit()
    new_expiry = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)

    def fake_refresh(self, _request):
        self.token = "new-access-token"
        self.expiry = new_expiry

    with patch("google.oauth2.credentials.Credentials.refresh", fake_refresh):
        creds = google_sync.load_credentials(account, db_session)

    assert creds.token == "new-access-token"
    stored = json.loads(decrypt_secret(account.oauth_token_encrypted))
    assert stored["token"] == "new-access-token" and stored["expiry"] == new_expiry.isoformat()
    assert stored["refresh_token"] == "refresh-token-xyz"
    assert "new-access-token" not in account.oauth_token_encrypted  # encrypted at rest


def test_load_credentials_does_not_refresh_a_valid_token(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    before = account.oauth_token_encrypted

    creds = google_sync.load_credentials(account, db_session)  # the autouse guard fails on any refresh attempt

    assert creds.token == "access-token-abc" and account.oauth_token_encrypted == before


def test_load_credentials_refreshes_legacy_tokens_stored_without_expiry(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    account.oauth_token_encrypted = encrypt_secret(valid_creds_json(expiry=None))
    db_session.commit()
    refreshed = []

    def fake_refresh(self, _request):
        refreshed.append(True)
        self.token = "fresh"
        self.expiry = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)

    with patch("google.oauth2.credentials.Credentials.refresh", fake_refresh):
        google_sync.load_credentials(account, db_session)

    assert refreshed and json.loads(decrypt_secret(account.oauth_token_encrypted))["expiry"] is not None


def test_load_credentials_error_mapping(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])
    account.oauth_token_encrypted = encrypt_secret(
        valid_creds_json(expiry=(datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat())
    )
    db_session.commit()

    with patch("google.oauth2.credentials.Credentials.refresh", side_effect=RefreshError("invalid_grant")):
        with pytest.raises(NeedsReauth):
            google_sync.load_credentials(account, db_session)
    with patch("google.oauth2.credentials.Credentials.refresh", side_effect=RefreshError("Temporary failure")):
        with pytest.raises(RefreshError):
            google_sync.load_credentials(account, db_session)

    account.oauth_token_encrypted = None
    with pytest.raises(NeedsReauth):
        google_sync.load_credentials(account, db_session)


def test_revoke_token_posts_the_refresh_token_and_swallows_failures(db_session):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"])

    with patch("app.core.google_sync.httpx.post") as post:
        google_sync.revoke_token(account)
    args, kwargs = post.call_args
    assert args[0] == "https://oauth2.googleapis.com/revoke"
    assert kwargs["data"] == {"token": "refresh-token-xyz"} and kwargs["timeout"] <= 10

    with patch("app.core.google_sync.httpx.post", side_effect=RuntimeError("offline")):
        google_sync.revoke_token(account)  # must not raise


# --------------------------------------------------------------------------------------------- worker


def _use_worker_session(db_session, monkeypatch, fake):
    from app.worker import sync_worker

    monkeypatch.setattr(sync_worker, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.core.google_sync.discovery.build", lambda *a, **k: fake)
    return sync_worker


def test_worker_syncs_selected_calendars_through_the_core_module(db_session, monkeypatch):
    f = make_family(db_session)
    account = make_account(db_session, f["papa"], owners={"primary": [f["leo"].id]})
    fake = FakeGoogle()
    fake.set_events("primary", [timed("e1", "Schule", soon())])
    worker = _use_worker_session(db_session, monkeypatch, fake)

    worker.sync_google_calendars()

    assert _by_title(_rows(db_session, f["family"])) == {("Schule", f["leo"].id)}
    db_session.refresh(account)
    assert account.status == "ok" and account.last_synced_at is not None


def test_worker_skips_accounts_that_need_reauth(db_session, monkeypatch):
    f = make_family(db_session)
    make_account(db_session, f["papa"], status="needs_reauth", last_error="Bitte neu verbinden")
    ok_account = make_account(db_session, f["mama"])
    fake = FakeGoogle()
    fake.set_events("primary", [timed("e1", "X", soon())])
    worker = _use_worker_session(db_session, monkeypatch, fake)

    worker.sync_google_calendars()

    assert [r.sync_account_id for r in _rows(db_session, f["family"])] == [ok_account.id]


def test_google_job_runs_every_fifteen_minutes_and_is_not_paused():
    from app.worker import sync_worker

    scheduler = sync_worker.build_scheduler()
    job = next(j for j in scheduler.get_jobs() if j.func is sync_worker.sync_google_calendars)

    assert job.trigger.interval == timedelta(minutes=15)
    assert job.next_run_time is not None  # next_run_time=None would add the job paused (it would never run)
