"""Google -> school breaks: all-day holiday events become school_break rows instead of calendar events.
No network: the Google service is the fake from tests/google_helpers.py."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.timetable_calendar import replace_timetable_calendar_events
from app.models.calendar import CalendarEvent
from app.models.school_break import SchoolBreak
from app.models.timetable import TimetableEntry
from tests.google_helpers import FakeGoogle, all_day, headers_for, make_account, make_family, sync, timed

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
D = date(2026, 10, 12)  # first day of the autumn holidays used below


@pytest.fixture()
def fam(db_session):
    return make_family(db_session)


def breaks(db):
    return list(db.scalars(select(SchoolBreak).order_by(SchoolBreak.start_date, SchoolBreak.title)))


def events(db):
    return list(db.scalars(select(CalendarEvent)))


def run(db, fam, fake, *, owners=None, account=None, now=NOW):
    account = account or make_account(db, fam["papa"], owners=owners)
    sync(db, account, fake, now=now)
    return account


def fake_with(*items, calendar="primary"):
    fake = FakeGoogle()
    fake.set_events(calendar, list(items))
    return fake


# ------------------------------------------------------------------------------------- detection table


@pytest.mark.parametrize(
    ("summary", "title", "kind", "child_keys"),
    [
        ("Herbstferien", "Herbstferien", "ferien", []),
        ("Ferien: Herbst", "Ferien: Herbst", "ferien", []),
        ("Sommerferien", "Sommerferien", "ferien", []),
        ("herbstFERIEN", "herbstFERIEN", "ferien", []),
        ("Mia: Ferien", "Ferien", "ferien", ["mia"]),
        ("Mia und Leo: Herbstferien", "Herbstferien", "ferien", ["mia", "leo"]),
        ("Herbstferien #Leo", "Herbstferien", "ferien", ["leo"]),
        ("Papa: Ferien", "Ferien", "ferien", []),  # a parent tag means "all children"
        ("Mia, Papa: Ferien", "Ferien", "ferien", ["mia"]),
        ("Schulfrei", "Schulfrei", "schulfrei", []),
        ("schulfrei: Fortbildung", "schulfrei: Fortbildung", "schulfrei", []),
        ("Schule frei", "Schule frei", "schulfrei", []),
        ("Leo: Unterrichtsfrei", "Unterrichtsfrei", "schulfrei", ["leo"]),
        ("Ferienwohnung buchen", "Ferienwohnung buchen", "ferien", []),  # known limitation (all-day)
    ],
)
def test_all_day_events_with_holiday_titles_become_breaks(db_session, fam, summary, title, kind, child_keys):
    run(db_session, fam, fake_with(all_day("e1", summary, D, D + timedelta(days=1))))

    [brk] = breaks(db_session)
    assert (brk.title, brk.kind, brk.source) == (title, kind, "google")
    assert brk.child_user_ids == [fam[key].id for key in child_keys]
    assert (brk.start_date, brk.end_date) == (D, D)
    assert events(db_session) == []  # instead of a normal calendar event


@pytest.mark.parametrize(
    "summary", ["Zahnarzt", "Schule", "Elternabend", "Heute schulfrei?", "Mia: Zahnarzt"]
)
def test_other_all_day_events_stay_normal_events(db_session, fam, summary):
    run(db_session, fam, fake_with(all_day("e1", summary, D, D + timedelta(days=1))))

    assert breaks(db_session) == []
    assert len(events(db_session)) == 1


def test_a_timed_event_is_never_a_break(db_session, fam):
    start = datetime(2026, 10, 12, 10, tzinfo=UTC)
    run(db_session, fam, fake_with(timed("e1", "Ferienwohnung buchen", start)))

    assert breaks(db_session) == []
    assert [e.title for e in events(db_session)] == ["Ferienwohnung buchen"]


def test_private_events_are_not_turned_into_breaks(db_session, fam):
    run(db_session, fam, fake_with(all_day("e1", "Ferien", D, D + timedelta(days=1), visibility="private")))

    assert breaks(db_session) == []
    assert len(events(db_session)) == 1


def test_cancelled_events_create_nothing(db_session, fam):
    run(db_session, fam, fake_with(all_day("e1", "Ferien", D, D + timedelta(days=1), status="cancelled")))

    assert breaks(db_session) == [] and events(db_session) == []


# ----------------------------------------------------------------------------------------- end dates


def test_the_exclusive_google_end_becomes_an_inclusive_last_day(db_session, fam):
    # 12.10. - 23.10. (two weeks minus the weekend): Google sends end.date = 24.10.
    run(db_session, fam, fake_with(all_day("e1", "Herbstferien", D, date(2026, 10, 24))))

    [brk] = breaks(db_session)
    assert (brk.start_date, brk.end_date) == (date(2026, 10, 12), date(2026, 10, 23))


def test_a_missing_or_odd_end_means_a_single_day(db_session, fam):
    odd = all_day("e1", "Ferien", D, D)  # end == start
    missing = {"id": "e2", "summary": "Schulfrei", "start": {"date": "2026-10-20"}, "end": {}}
    run(db_session, fam, fake_with(odd, missing))

    assert [(b.title, b.start_date, b.end_date) for b in breaks(db_session)] == [
        ("Ferien", D, D),
        ("Schulfrei", date(2026, 10, 20), date(2026, 10, 20)),
    ]


def test_an_event_that_started_before_the_window_keeps_its_real_dates(db_session, fam):
    # Google returns every event that overlaps timeMin..timeMax, so a break that began 3 weeks ago is listed
    # with its true start: it is stored as is and still covers today.
    start = date(2026, 9, 14)
    fake = fake_with(all_day("e1", "Herbstferien", start, date(2026, 10, 6)))

    run(db_session, fam, fake, now=NOW)

    [brk] = breaks(db_session)
    assert (brk.start_date, brk.end_date) == (date(2026, 9, 14), date(2026, 10, 5))
    call = fake.event_calls[0]
    assert call["timeMin"] < call["timeMax"] and call["singleEvents"] is True


def test_absurdly_long_all_day_events_stay_normal_events(db_session, fam):
    run(db_session, fam, fake_with(all_day("e1", "Ferien", D, D + timedelta(days=500))))

    assert breaks(db_session) == [] and len(events(db_session)) == 1


def test_long_titles_are_cut_to_the_column_size(db_session, fam):
    run(db_session, fam, fake_with(all_day("e1", "Ferien " + "x" * 300, D, D + timedelta(days=1))))

    [brk] = breaks(db_session)
    assert len(brk.title) <= 100 and brk.title.startswith("Ferien xxx")


# ------------------------------------------------------------------------------ children resolution


def test_default_owners_of_the_calendar_decide_the_children(db_session, fam):
    fake = fake_with(all_day("e1", "Ferien", D, D + timedelta(days=1)))

    run(db_session, fam, fake, owners={"primary": [fam["mia"].id]})

    assert breaks(db_session)[0].child_user_ids == [fam["mia"].id]


def test_a_tag_wins_over_the_default_owners(db_session, fam):
    fake = fake_with(all_day("e1", "Leo: Ferien", D, D + timedelta(days=1)))

    run(db_session, fam, fake, owners={"primary": [fam["mia"].id]})

    assert breaks(db_session)[0].child_user_ids == [fam["leo"].id]


def test_parent_default_owners_mean_all_children(db_session, fam):
    fake = fake_with(all_day("e1", "Ferien", D, D + timedelta(days=1)))

    run(db_session, fam, fake, owners={"primary": [fam["papa"].id]})

    assert breaks(db_session)[0].child_user_ids == []


# ----------------------------------------------------------------------------------- upsert/cleanup


def test_a_second_sync_updates_instead_of_duplicating(db_session, fam):
    account = run(db_session, fam, fake_with(all_day("e1", "Herbstferien", D, D + timedelta(days=5))))
    first_id = breaks(db_session)[0].id

    sync(db_session, account, fake_with(all_day("e1", "Mia: Herbstferien 2026", D, D + timedelta(days=8))), now=NOW)

    [brk] = breaks(db_session)
    assert brk.id == first_id
    assert (brk.title, brk.end_date, brk.child_user_ids) == (
        "Herbstferien 2026", D + timedelta(days=7), [fam["mia"].id]
    )


def test_breaks_that_vanish_are_deleted(db_session, fam):
    account = run(db_session, fam, fake_with(
        all_day("e1", "Herbstferien", D, D + timedelta(days=5)),
        all_day("e2", "Schulfrei", D + timedelta(days=20), D + timedelta(days=21)),
    ))
    assert len(breaks(db_session)) == 2

    sync(db_session, account, fake_with(all_day("e1", "Herbstferien", D, D + timedelta(days=5))), now=NOW)

    assert [b.external_id for b in breaks(db_session)] == ["e1"]


def test_cancelled_breaks_are_deleted(db_session, fam):
    account = run(db_session, fam, fake_with(all_day("e1", "Ferien", D, D + timedelta(days=2))))

    sync(db_session, account, fake_with(all_day("e1", "Ferien", D, D + timedelta(days=2), status="cancelled")), now=NOW)

    assert breaks(db_session) == []


def test_a_break_that_no_longer_matches_becomes_a_normal_event(db_session, fam):
    account = run(db_session, fam, fake_with(all_day("e1", "Ferien", D, D + timedelta(days=2))))

    sync(db_session, account, fake_with(all_day("e1", "Zahnarzt", D, D + timedelta(days=2))), now=NOW)

    assert breaks(db_session) == []
    assert [e.title for e in events(db_session)] == ["Zahnarzt"]


def test_an_event_that_became_a_break_loses_its_calendar_row(db_session, fam):
    account = run(db_session, fam, fake_with(all_day("e1", "Termin", D, D + timedelta(days=2))))
    assert [e.title for e in events(db_session)] == ["Termin"]

    sync(db_session, account, fake_with(all_day("e1", "Herbstferien", D, D + timedelta(days=2))), now=NOW)

    assert events(db_session) == []
    assert [b.title for b in breaks(db_session)] == ["Herbstferien"]


def test_a_truncated_import_only_drops_breaks_it_saw_again(db_session, fam):
    account = run(db_session, fam, fake_with(
        all_day("e1", "Ferien", D, D + timedelta(days=2)),
        all_day("e2", "Schulfrei", D + timedelta(days=20), D + timedelta(days=21)),
    ))
    # Page limit reached: 26 pages of 1 event, the calendar has more. e2 is not seen at all, e1 changed type.
    fake = FakeGoogle()
    fake.set_events("primary", [all_day("e1", "Zahnarzt", D, D + timedelta(days=2))] + [
        timed(f"t{i}", "x", NOW + timedelta(days=1, hours=i)) for i in range(25)
    ], pages=26)

    sync(db_session, account, fake, now=NOW)

    assert account.last_error == "Zu viele Termine: nur ein Teil wurde importiert."  # really truncated
    assert [b.external_id for b in breaks(db_session)] == ["e2"]


def test_manual_breaks_and_other_calendars_are_never_touched(db_session, fam):
    manual = SchoolBreak(family_id=fam["family"].id, title="Manuell", start_date=D, end_date=D, source="manual")
    db_session.add(manual)
    db_session.commit()
    account = make_account(db_session, fam["papa"], selections=("primary", "cal-b"))
    fake = FakeGoogle()
    fake.set_events("primary", [all_day("e1", "Ferien", D, D + timedelta(days=2))])
    fake.set_events("cal-b", [all_day("e9", "Schulfrei", D, D + timedelta(days=2))])
    sync(db_session, account, fake, now=NOW)
    assert {b.external_id for b in breaks(db_session) if b.source == "google"} == {"e1", "e9"}

    fake.set_events("primary", [])  # primary is emptied, cal-b still has its break
    sync(db_session, account, fake, now=NOW)

    assert {(b.source, b.external_id) for b in breaks(db_session)} == {("manual", None), ("google", "e9")}


def test_deselecting_a_calendar_removes_its_breaks_and_restores_lessons(client, db_session, fam, monkeypatch):
    monkeypatch.setattr(
        "app.core.google_sync.discovery.build", lambda *a, **k: FakeGoogle()
    )
    account = make_account(db_session, fam["papa"], selections=("cal-a",))
    brk_start = date.today() + timedelta(days=3)
    fake = FakeGoogle()
    fake.set_events("cal-a", [all_day("e1", "Ferien", brk_start, brk_start + timedelta(days=3))])
    sync(db_session, account, fake, now=datetime.now(UTC))
    assert len(breaks(db_session)) == 1
    _timetable(db_session, fam)
    replace_timetable_calendar_events(db_session, fam["family"].id, fam["mia"].id)
    assert not _lesson_dates(db_session, fam) & {brk_start}

    response = client.put(
        "/calendar/sync/google/calendars", json={"calendars": []}, headers=headers_for(fam["papa"])
    )

    assert response.status_code == 200
    assert breaks(db_session) == []
    assert brk_start in _lesson_dates(db_session, fam)


def _timetable(db, fam):
    for weekday in range(7):
        db.add(TimetableEntry(
            family_id=fam["family"].id, child_user_id=fam["mia"].id, day_of_week=weekday,
            start_time="08:00", end_time="09:00", subject="Mathe", room=None,
        ))
    db.commit()


def _lesson_dates(db, fam) -> set[date]:
    rows = db.scalars(select(CalendarEvent).where(
        CalendarEvent.owner_user_id == fam["mia"].id, CalendarEvent.source == "timetable"))
    return {date.fromisoformat(r.external_id.rsplit(":", 1)[1]) for r in rows}


# ----------------------------------------------------------------------------------------- isolation


def test_no_leakage_across_accounts_or_families(db_session, fam):
    other = make_family(db_session, name="Andere", tag="b")
    mine = make_account(db_session, fam["papa"])
    theirs = make_account(db_session, other["papa"])

    sync(db_session, theirs, fake_with(all_day("x1", "Ferien", D, D + timedelta(days=2))), now=NOW)
    sync(db_session, mine, fake_with(), now=NOW)  # empty calendar: must not delete the other family's break

    [brk] = breaks(db_session)
    assert brk.family_id == other["family"].id and brk.sync_account_id == theirs.id
    # ... and the other family's break is invisible to this family
    assert breaks_for_family(db_session, fam["family"].id) == []


def breaks_for_family(db, family_id):
    return list(db.scalars(select(SchoolBreak).where(SchoolBreak.family_id == family_id)))


def test_two_accounts_of_one_family_do_not_duplicate_a_shared_calendar_event(db_session, fam):
    first = make_account(db_session, fam["papa"])
    second = make_account(db_session, fam["mama"])
    fake = fake_with(all_day("e1", "Ferien", D, D + timedelta(days=2)))

    sync(db_session, first, fake, now=NOW)
    sync(db_session, second, fake, now=NOW)

    [brk] = breaks(db_session)
    assert brk.sync_account_id == first.id
    sync(db_session, second, fake_with(), now=NOW)  # the second account must not remove the first one's row
    assert len(breaks(db_session)) == 1


# --------------------------------------------------------------------------- timetable reconcile


def test_importing_and_removing_a_break_adjusts_the_timetable_events(db_session, fam):
    _timetable(db_session, fam)
    replace_timetable_calendar_events(db_session, fam["family"].id, fam["mia"].id)
    first = date.today() + timedelta(days=3)
    account = make_account(db_session, fam["papa"], owners={"primary": [fam["mia"].id]})
    holiday = all_day("e1", "Herbstferien", first, first + timedelta(days=5))  # 5 days: first..first+4

    sync(db_session, account, fake_with(holiday), now=datetime.now(UTC))
    covered = {first + timedelta(days=i) for i in range(5)}
    assert not _lesson_dates(db_session, fam) & covered
    assert first + timedelta(days=5) in _lesson_dates(db_session, fam)

    sync(db_session, account, fake_with(), now=datetime.now(UTC))
    assert breaks(db_session) == []
    assert covered <= _lesson_dates(db_session, fam)


def test_a_break_for_leo_leaves_mias_lessons(db_session, fam):
    _timetable(db_session, fam)
    replace_timetable_calendar_events(db_session, fam["family"].id, fam["mia"].id)
    before = _lesson_dates(db_session, fam)
    first = date.today() + timedelta(days=3)
    account = make_account(db_session, fam["papa"])

    sync(db_session, account, fake_with(all_day("e1", "Leo: Ferien", first, first + timedelta(days=3))),
         now=datetime.now(UTC))

    assert _lesson_dates(db_session, fam) == before
