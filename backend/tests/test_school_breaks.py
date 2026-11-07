"""School holidays / free days: CRUD API, permissions, tenancy, validation, the child view, and the timetable
calendar events that must follow the breaks (API, reconcile function and the worker top-up)."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.school_breaks import affected_children, applies_to_child, classify_break_title, covered_dates
from app.core.timetable_calendar import (
    extend_timetable_calendar_events,
    reconcile_family_timetables,
    replace_timetable_calendar_events,
)
from app.models.calendar import CalendarEvent
from app.models.school_break import SchoolBreak
from app.models.timetable import TimetableEntry
from tests.google_helpers import headers_for, make_account, make_family

URL = "/school-breaks"
TODAY = date.today()


def d(offset: int) -> date:
    return TODAY + timedelta(days=offset)


def body(**overrides) -> dict:
    values = {"title": "Herbstferien", "kind": "ferien", "start_date": d(3).isoformat(), "end_date": d(9).isoformat()}
    values.update(overrides)
    return values


@pytest.fixture()
def fam(db_session):
    return make_family(db_session)


@pytest.fixture()
def other(db_session):
    return make_family(db_session, name="Andere", tag="b")


def add_break(db, family, *, children=(), start=3, end=9, title="Ferien", source="manual", **extra) -> SchoolBreak:
    row = SchoolBreak(
        family_id=family.id,
        title=title,
        kind="ferien",
        start_date=d(start),
        end_date=d(end),
        child_user_ids=[c.id for c in children],
        source=source,
        **extra,
    )
    db.add(row)
    db.commit()
    return row


def add_timetable(db, family, child) -> None:
    """One lesson on every weekday: every date of the window has exactly one lesson event."""
    for weekday in range(7):
        db.add(
            TimetableEntry(
                family_id=family.id, child_user_id=child.id, day_of_week=weekday,
                start_time="08:00", end_time="09:00", subject="Mathe", room=None,
            )
        )
    db.commit()
    replace_timetable_calendar_events(db, family.id, child.id, today=TODAY)


def lesson_days(db, child) -> set[date]:
    rows = db.scalars(
        select(CalendarEvent).where(CalendarEvent.owner_user_id == child.id, CalendarEvent.source == "timetable")
    )
    return {date.fromisoformat(row.external_id.rsplit(":", 1)[1]) for row in rows}


def days(first: int, last: int) -> set[date]:
    return {d(i) for i in range(first, last + 1)}


WINDOW = days(0, 41)


# ------------------------------------------------------------------------------------------- access


@pytest.mark.parametrize("method", ["get", "post"])
def test_unauthenticated_requests_get_401(client, method):
    response = client.request(method.upper(), URL, json=body() if method == "post" else None)
    assert response.status_code == 401


@pytest.mark.parametrize("who", ["mia", "wall"])
def test_children_and_wall_cannot_write(client, db_session, fam, who):
    brk = add_break(db_session, fam["family"])
    headers = headers_for(fam[who])

    assert client.post(URL, json=body(), headers=headers).status_code == 403
    assert client.patch(f"{URL}/{brk.id}", json={"title": "X"}, headers=headers).status_code == 403
    assert client.delete(f"{URL}/{brk.id}", headers=headers).status_code == 403
    assert db_session.get(SchoolBreak, brk.id).title == "Ferien"


@pytest.mark.parametrize("who", ["papa", "mama"])
def test_both_parent_roles_can_write(client, db_session, fam, who):
    headers = headers_for(fam[who])

    created = client.post(URL, json=body(), headers=headers)
    assert created.status_code == 201
    break_id = created.json()["id"]
    assert client.patch(f"{URL}/{break_id}", json={"title": "Neu"}, headers=headers).json()["title"] == "Neu"
    assert client.delete(f"{URL}/{break_id}", headers=headers).status_code == 204


# --------------------------------------------------------------------------------------------- CRUD


def test_create_returns_the_break_for_selected_children(client, fam):
    response = client.post(
        URL,
        json=body(title="  Herbst   ferien ", child_user_ids=[str(fam["mia"].id), str(fam["mia"].id)]),
        headers=headers_for(fam["papa"]),
    )

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Herbst ferien"
    assert data["kind"] == "ferien" and data["source"] == "manual"
    assert data["start_date"] == d(3).isoformat() and data["end_date"] == d(9).isoformat()
    assert data["child_user_ids"] == [str(fam["mia"].id)]
    assert "sync_account_id" not in data and "family_id" not in data


def test_create_defaults_to_ferien_for_all_children(client, fam):
    payload = {"title": "Frei", "start_date": d(1).isoformat(), "end_date": d(1).isoformat()}

    data = client.post(URL, json=payload, headers=headers_for(fam["papa"])).json()

    assert data["kind"] == "ferien" and data["child_user_ids"] == []


def test_create_schulfrei_single_day(client, fam):
    payload = body(kind="schulfrei", start_date=d(5).isoformat(), end_date=d(5).isoformat())
    assert client.post(URL, json=payload, headers=headers_for(fam["papa"])).json()["kind"] == "schulfrei"


def test_list_is_sorted_by_start_and_limited_to_the_default_window(client, db_session, fam):
    add_break(db_session, fam["family"], start=20, end=22, title="B")
    add_break(db_session, fam["family"], start=-3, end=2, title="A")
    add_break(db_session, fam["family"], start=-60, end=-50, title="zu alt")
    add_break(db_session, fam["family"], start=200, end=210, title="zu weit")

    titles = [b["title"] for b in client.get(URL, headers=headers_for(fam["papa"])).json()]

    assert titles == ["A", "B"]


def test_list_filters_by_explicit_window_and_includes_overlaps(client, db_session, fam):
    add_break(db_session, fam["family"], start=-10, end=5, title="laufend")
    add_break(db_session, fam["family"], start=30, end=31, title="spaeter")

    def titles(**params):
        response = client.get(URL, params=params, headers=headers_for(fam["papa"]))
        assert response.status_code == 200
        return [b["title"] for b in response.json()]

    assert titles(**{"from": d(0).isoformat(), "to": d(10).isoformat()}) == ["laufend"]
    assert titles(**{"from": d(6).isoformat(), "to": d(29).isoformat()}) == []
    assert titles(**{"from": d(31).isoformat(), "to": d(40).isoformat()}) == ["spaeter"]


def test_list_caps_the_window_and_rejects_reversed_ranges(client, db_session, fam):
    add_break(db_session, fam["family"], start=500, end=501, title="weit")
    headers = headers_for(fam["papa"])

    capped = client.get(URL, params={"from": d(0).isoformat(), "to": d(5000).isoformat()}, headers=headers)
    assert capped.status_code == 200 and capped.json() == []
    reversed_ = client.get(URL, params={"from": d(10).isoformat(), "to": d(1).isoformat()}, headers=headers)
    assert reversed_.status_code == 422
    assert client.get(URL, params={"from": "morgen"}, headers=headers).status_code == 422


def test_parents_and_dashboard_see_every_child_id(client, db_session, fam):
    add_break(db_session, fam["family"], children=[fam["mia"], fam["leo"]])
    expected = {str(fam["mia"].id), str(fam["leo"].id)}

    for who in ("papa", "mama", "wall"):
        rows = client.get(URL, headers=headers_for(fam[who])).json()
        assert len(rows) == 1 and set(rows[0]["child_user_ids"]) == expected


def test_a_child_only_sees_its_breaks_and_never_sibling_ids(client, db_session, fam):
    add_break(db_session, fam["family"], title="alle")
    add_break(db_session, fam["family"], children=[fam["mia"]], title="nur Mia", start=10, end=11)
    add_break(db_session, fam["family"], children=[fam["leo"]], title="nur Leo", start=12, end=13)
    add_break(db_session, fam["family"], children=[fam["leo"], fam["mia"]], title="beide", start=14, end=15)

    rows = client.get(URL, headers=headers_for(fam["mia"])).json()

    assert {r["title"]: r["child_user_ids"] for r in rows} == {
        "alle": [],
        "nur Mia": [str(fam["mia"].id)],
        "beide": [str(fam["mia"].id)],
    }
    assert str(fam["leo"].id) not in str(rows)


def test_patch_updates_fields_and_keeps_the_rest(client, db_session, fam):
    brk = add_break(db_session, fam["family"], children=[fam["mia"]])

    data = client.patch(
        f"{URL}/{brk.id}",
        json={"title": "Sport", "kind": "schulfrei", "end_date": d(4).isoformat()},
        headers=headers_for(fam["papa"]),
    ).json()

    assert (data["title"], data["kind"], data["end_date"]) == ("Sport", "schulfrei", d(4).isoformat())
    assert data["start_date"] == d(3).isoformat() and data["child_user_ids"] == [str(fam["mia"].id)]


def test_patch_with_an_empty_child_list_means_all_children(client, db_session, fam):
    brk = add_break(db_session, fam["family"], children=[fam["mia"]])

    data = client.patch(f"{URL}/{brk.id}", json={"child_user_ids": []}, headers=headers_for(fam["papa"])).json()

    assert data["child_user_ids"] == []


def test_delete_removes_the_break(client, db_session, fam):
    brk = add_break(db_session, fam["family"])
    break_id = brk.id

    assert client.delete(f"{URL}/{break_id}", headers=headers_for(fam["papa"])).status_code == 204

    db_session.expire_all()
    assert db_session.get(SchoolBreak, break_id) is None
    assert client.delete(f"{URL}/{break_id}", headers=headers_for(fam["papa"])).status_code == 404


def test_google_rows_cannot_be_changed_or_deleted(client, db_session, fam):
    account = make_account(db_session, fam["papa"])
    brk = add_break(
        db_session, fam["family"], source="google", sync_account_id=account.id,
        source_calendar_id="primary", external_id="e1",
    )
    headers = headers_for(fam["papa"])

    patched = client.patch(f"{URL}/{brk.id}", json={"title": "X"}, headers=headers)
    deleted = client.delete(f"{URL}/{brk.id}", headers=headers)

    assert patched.status_code == 409 and deleted.status_code == 409
    assert patched.json()["detail"] == "Dieser Eintrag kommt aus Google und wird dort geändert."
    assert db_session.get(SchoolBreak, brk.id).title == "Ferien"
    listed = client.get(URL, headers=headers).json()
    assert listed[0]["source"] == "google"


# ------------------------------------------------------------------------------------------ tenancy


def test_another_family_cannot_read_change_or_delete_a_break(client, db_session, fam, other):
    brk = add_break(db_session, fam["family"], children=[fam["mia"]])
    attacker = headers_for(other["papa"])

    assert client.get(URL, headers=attacker).json() == []
    assert client.get(URL, headers=headers_for(other["mia"])).json() == []
    assert client.patch(f"{URL}/{brk.id}", json={"title": "Hack"}, headers=attacker).status_code == 404
    assert client.delete(f"{URL}/{brk.id}", headers=attacker).status_code == 404
    db_session.expire_all()
    assert db_session.get(SchoolBreak, brk.id).title == "Ferien"


def test_child_ids_of_another_family_or_of_a_parent_are_rejected(client, db_session, fam, other):
    headers = headers_for(fam["papa"])
    brk = add_break(db_session, fam["family"])

    for bad in (other["mia"], fam["papa"], fam["wall"]):
        assert client.post(URL, json=body(child_user_ids=[str(bad.id)]), headers=headers).status_code == 404
        patched = client.patch(f"{URL}/{brk.id}", json={"child_user_ids": [str(bad.id)]}, headers=headers)
        assert patched.status_code == 404
    assert client.post(URL, json=body(child_user_ids=[str(uuid.uuid4())]), headers=headers).status_code == 404
    assert db_session.scalars(select(SchoolBreak).where(SchoolBreak.family_id == fam["family"].id)).all() == [brk]
    assert db_session.get(SchoolBreak, brk.id).child_user_ids == []


def test_unknown_break_id_is_404_and_a_malformed_one_422(client, fam):
    headers = headers_for(fam["papa"])
    assert client.patch(f"{URL}/{uuid.uuid4()}", json={"title": "X"}, headers=headers).status_code == 404
    assert client.delete(f"{URL}/{uuid.uuid4()}", headers=headers).status_code == 404
    assert client.patch(f"{URL}/nope", json={"title": "X"}, headers=headers).status_code == 422
    assert client.delete(f"{URL}/nope", headers=headers).status_code == 422


# ---------------------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 101},
        {"kind": "urlaub"},
        {"start_date": "kein-datum"},
        {"start_date": d(10).isoformat(), "end_date": d(9).isoformat()},
        {"start_date": d(0).isoformat(), "end_date": d(400).isoformat()},  # 401 days
        {"child_user_ids": ["keine-uuid"]},
        {"child_user_ids": [str(uuid.uuid4())] * 51},
    ],
)
def test_create_validation_returns_422(client, db_session, fam, overrides):
    response = client.post(URL, json=body(**overrides), headers=headers_for(fam["papa"]))

    assert response.status_code == 422
    assert db_session.scalars(select(SchoolBreak)).all() == []


def test_a_span_of_exactly_400_days_is_allowed(client, fam):
    payload = body(start_date=d(0).isoformat(), end_date=d(399).isoformat())
    assert client.post(URL, json=payload, headers=headers_for(fam["papa"])).status_code == 201


def test_missing_fields_are_422(client, fam):
    assert client.post(URL, json={"title": "X"}, headers=headers_for(fam["papa"])).status_code == 422


@pytest.mark.parametrize(
    "changes",
    [
        {"title": None},
        {"title": " "},
        {"kind": "x"},
        {"start_date": None},
        {"child_user_ids": None},
        {"start_date": d(20).isoformat(), "end_date": d(10).isoformat()},
        {"end_date": d(1).isoformat()},  # before the stored start (day 3)
        {"start_date": d(30).isoformat()},  # after the stored end (day 9)
        {"start_date": d(-500).isoformat()},  # span over 400 days
    ],
)
def test_patch_validation_returns_422_and_changes_nothing(client, db_session, fam, changes):
    brk = add_break(db_session, fam["family"])

    response = client.patch(f"{URL}/{brk.id}", json=changes, headers=headers_for(fam["papa"]))

    assert response.status_code == 422
    db_session.expire_all()
    stored = db_session.get(SchoolBreak, brk.id)
    assert (stored.title, stored.start_date, stored.end_date) == ("Ferien", d(3), d(9))


# ------------------------------------------------------------------------------------ core helpers


def test_applies_to_child_and_affected_children():
    mia, leo = uuid.uuid4(), uuid.uuid4()
    brk = SchoolBreak(child_user_ids=[mia])
    assert applies_to_child(brk, mia) and not applies_to_child(brk, leo)
    assert applies_to_child(SchoolBreak(child_user_ids=[]), leo)
    assert affected_children([mia], [leo]) == {mia, leo}
    assert affected_children([mia], []) is None
    assert affected_children() is None


def test_covered_dates_clips_to_the_range_and_respects_children(db_session, fam, other):
    add_break(db_session, fam["family"], children=[fam["mia"]], start=-2, end=3)
    add_break(db_session, fam["family"], start=10, end=11)
    add_break(db_session, other["family"], start=0, end=40)  # another family never counts
    fid = fam["family"].id

    assert covered_dates(db_session, fid, fam["mia"].id, d(0), d(12)) == days(0, 3) | days(10, 11)
    assert covered_dates(db_session, fid, fam["leo"].id, d(0), d(12)) == days(10, 11)
    assert covered_dates(db_session, fid, fam["mia"].id, d(20), d(30)) == set()


# ---------------------------------------------------------------------------------------- reconcile


def test_replace_and_extend_skip_break_dates(db_session, fam):
    mia = fam["mia"]
    add_break(db_session, fam["family"], children=[mia], start=3, end=9)
    for weekday in range(7):
        db_session.add(
            TimetableEntry(
                family_id=fam["family"].id, child_user_id=mia.id, day_of_week=weekday,
                start_time="08:00", end_time="09:00", subject="Mathe", room=None,
            )
        )
    db_session.commit()

    replace_timetable_calendar_events(db_session, fam["family"].id, mia.id, today=TODAY)
    assert lesson_days(db_session, mia) == WINDOW - days(3, 9)

    db_session.query(CalendarEvent).filter_by(source="timetable").delete()
    db_session.commit()
    extend_timetable_calendar_events(db_session, fam["family"].id, mia.id, today=TODAY)
    assert lesson_days(db_session, mia) == WINDOW - days(3, 9)


def test_the_worker_job_skips_break_dates(db_session, fam, monkeypatch):
    from app.worker import sync_worker

    mia, leo = fam["mia"], fam["leo"]
    for child in (mia, leo):
        for weekday in range(7):
            db_session.add(
                TimetableEntry(
                    family_id=fam["family"].id, child_user_id=child.id, day_of_week=weekday,
                    start_time="08:00", end_time="09:00", subject="Mathe", room=None,
                )
            )
    db_session.commit()
    add_break(db_session, fam["family"], children=[mia], start=3, end=9)
    monkeypatch.setattr(sync_worker, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    sync_worker.generate_timetable_events()

    assert lesson_days(db_session, mia) == WINDOW - days(3, 9)
    assert lesson_days(db_session, leo) == WINDOW


def test_reconcile_removes_recreates_and_is_idempotent(db_session, fam):
    mia = fam["mia"]
    add_timetable(db_session, fam["family"], mia)
    assert lesson_days(db_session, mia) == WINDOW
    brk = add_break(db_session, fam["family"], children=[mia], start=3, end=9)

    reconcile_family_timetables(db_session, fam["family"].id, {mia.id}, today=TODAY)
    reconcile_family_timetables(db_session, fam["family"].id, {mia.id}, today=TODAY)
    assert lesson_days(db_session, mia) == WINDOW - days(3, 9)
    count = db_session.query(CalendarEvent).filter_by(source="timetable").count()
    assert count == len(WINDOW) - 7

    brk.end_date = d(5)  # shrunk
    db_session.commit()
    reconcile_family_timetables(db_session, fam["family"].id, None, today=TODAY)
    assert lesson_days(db_session, mia) == WINDOW - days(3, 5)

    brk.start_date, brk.end_date = d(20), d(22)  # moved
    db_session.commit()
    reconcile_family_timetables(db_session, fam["family"].id, None, today=TODAY)
    assert lesson_days(db_session, mia) == WINDOW - days(20, 22)

    db_session.delete(brk)  # removed
    db_session.commit()
    reconcile_family_timetables(db_session, fam["family"].id, None, today=TODAY)
    assert lesson_days(db_session, mia) == WINDOW
    assert db_session.query(CalendarEvent).filter_by(source="timetable").count() == len(WINDOW)


def test_reconcile_never_touches_past_events_native_events_or_other_families(db_session, fam, other):
    mia = fam["mia"]
    add_timetable(db_session, fam["family"], mia)
    add_timetable(db_session, other["family"], other["mia"])
    past = CalendarEvent(
        family_id=fam["family"].id, owner_user_id=mia.id, title="gestern", source="timetable",
        start=db_session.query(CalendarEvent).first().start - timedelta(days=60),
        end=db_session.query(CalendarEvent).first().end - timedelta(days=60),
        external_id=f"{uuid.uuid4()}:{d(-1).isoformat()}",
    )
    native = CalendarEvent(
        family_id=fam["family"].id, owner_user_id=mia.id, title="Zahnarzt", source="native",
        start=past.start + timedelta(days=64), end=past.end + timedelta(days=64),
    )
    db_session.add_all([past, native])
    add_break(db_session, fam["family"], start=-5, end=9)
    add_break(db_session, other["family"], start=-5, end=0)  # not an interference of family 1
    db_session.commit()

    reconcile_family_timetables(db_session, fam["family"].id, None, today=TODAY)

    assert db_session.get(CalendarEvent, past.id) is not None
    assert db_session.get(CalendarEvent, native.id) is not None
    assert lesson_days(db_session, mia) - {d(-1)} == WINDOW - days(0, 9)
    assert lesson_days(db_session, other["mia"]) == WINDOW  # other family untouched by this call


def test_reconcile_ignores_ids_that_are_no_children_of_the_family(db_session, fam, other):
    add_timetable(db_session, other["family"], other["mia"])
    add_break(db_session, other["family"], start=0, end=40)  # would remove everything if it were applied
    before = lesson_days(db_session, other["mia"])

    reconcile_family_timetables(db_session, fam["family"].id, {other["mia"].id}, today=TODAY)

    assert lesson_days(db_session, other["mia"]) == before


# ------------------------------------------------------------------------- reconcile through the API


def test_api_create_removes_lessons_of_the_selected_children_only(client, db_session, fam):
    mia, leo = fam["mia"], fam["leo"]
    add_timetable(db_session, fam["family"], mia)
    add_timetable(db_session, fam["family"], leo)

    response = client.post(URL, json=body(child_user_ids=[str(mia.id)]), headers=headers_for(fam["papa"]))

    assert response.status_code == 201
    assert lesson_days(db_session, mia) == WINDOW - days(3, 9)
    assert lesson_days(db_session, leo) == WINDOW


def test_api_create_for_all_children_affects_everybody(client, db_session, fam):
    add_timetable(db_session, fam["family"], fam["mia"])
    add_timetable(db_session, fam["family"], fam["leo"])

    client.post(URL, json=body(), headers=headers_for(fam["papa"]))

    assert lesson_days(db_session, fam["mia"]) == WINDOW - days(3, 9)
    assert lesson_days(db_session, fam["leo"]) == WINDOW - days(3, 9)


def test_api_patch_shrinks_moves_and_changes_children(client, db_session, fam):
    mia, leo = fam["mia"], fam["leo"]
    add_timetable(db_session, fam["family"], mia)
    add_timetable(db_session, fam["family"], leo)
    headers = headers_for(fam["papa"])
    break_id = client.post(URL, json=body(), headers=headers).json()["id"]
    assert lesson_days(db_session, leo) == WINDOW - days(3, 9)

    client.patch(f"{URL}/{break_id}", json={"end_date": d(5).isoformat()}, headers=headers)
    assert lesson_days(db_session, mia) == WINDOW - days(3, 5)
    assert lesson_days(db_session, leo) == WINDOW - days(3, 5)

    client.patch(f"{URL}/{break_id}", json={"child_user_ids": [str(mia.id)]}, headers=headers)
    assert lesson_days(db_session, mia) == WINDOW - days(3, 5)
    assert lesson_days(db_session, leo) == WINDOW  # no longer covered: lessons are back

    client.patch(
        f"{URL}/{break_id}", json={"start_date": d(20).isoformat(), "end_date": d(21).isoformat()}, headers=headers
    )
    assert lesson_days(db_session, mia) == WINDOW - days(20, 21)


def test_api_delete_brings_the_lessons_back(client, db_session, fam):
    mia = fam["mia"]
    add_timetable(db_session, fam["family"], mia)
    headers = headers_for(fam["papa"])
    break_id = client.post(URL, json=body(child_user_ids=[str(mia.id)]), headers=headers).json()["id"]
    assert lesson_days(db_session, mia) == WINDOW - days(3, 9)

    assert client.delete(f"{URL}/{break_id}", headers=headers).status_code == 204

    assert lesson_days(db_session, mia) == WINDOW


def test_a_timetable_edit_during_a_break_does_not_bring_lessons_back(client, db_session, fam):
    mia = fam["mia"]
    add_timetable(db_session, fam["family"], mia)
    headers = headers_for(fam["papa"])
    client.post(URL, json=body(child_user_ids=[str(mia.id)]), headers=headers)

    response = client.post(
        "/timetables",
        json={"child_user_id": str(mia.id), "day_of_week": 2, "start_time": "10:00", "end_time": "11:00",
              "subject": "Musik"},
        headers=headers,
    )

    assert response.status_code == 201
    rows = db_session.scalars(
        select(CalendarEvent).where(CalendarEvent.owner_user_id == mia.id, CalendarEvent.source == "timetable")
    ).all()
    on_break = [r for r in rows if d(3) <= date.fromisoformat(r.external_id.rsplit(":", 1)[1]) <= d(9)]
    assert on_break == []


# ------------------------------------------------------------------------------------- title matching


@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("Herbstferien", "ferien"),
        ("Ferien: Herbst", "ferien"),
        ("Sommerferien 2026", "ferien"),
        ("FERIEN", "ferien"),
        ("Schulfrei", "schulfrei"),
        ("schulfrei (Lehrerfortbildung)", "schulfrei"),
        ("Schule frei", "schulfrei"),
        ("Unterrichtsfrei", "schulfrei"),
        ("Schulfreier Tag", "schulfrei"),
        ("  - Schulfrei", "schulfrei"),
        ("Ferienwohnung buchen", "ferien"),  # known limitation: matches as well
        ("Zahnarzt", None),
        ("Schule", None),
        ("Heute schulfrei?", None),  # only a title that starts with it
        ("Elternabend", None),
        ("", None),
    ],
)
def test_classify_break_title(title, kind):
    assert classify_break_title(title) == kind
