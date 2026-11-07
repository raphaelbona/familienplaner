"""Day phases (Tagesphasen): CRUD API, permissions, tenancy, validation, defaults and the delete behaviour."""

import uuid
from datetime import time

import pytest
from sqlalchemy import select

from app.models.day_phase import DayPhase
from app.models.task import TaskInstance, TaskStatus, task_template_phase
from tests.google_helpers import headers_for, make_family
from tests.phase_helpers import MON, add_assignment, add_instance, add_phase, add_template, add_timetable

URL = "/day-phases"


def body(**overrides) -> dict:
    values = {
        "name": "Vor der Schule",
        "icon": "🌅",
        "start_time": "06:00",
        "end_time": "08:00",
        "day_type": "school",
        "sort_order": 0,
    }
    values.update(overrides)
    return values


@pytest.fixture()
def fam(db_session):
    return make_family(db_session)


@pytest.fixture()
def other(db_session):
    return make_family(db_session, name="Andere", tag="b")


# ------------------------------------------------------------------------------------------- access


@pytest.mark.parametrize("method", ["get", "post"])
def test_unauthenticated_requests_get_401(client, method):
    response = client.request(method.upper(), URL, json=body() if method == "post" else None)
    assert response.status_code == 401


@pytest.mark.parametrize("who", ["mia", "wall"])
def test_children_and_wall_can_read_but_not_write(client, db_session, fam, who):
    phase = add_phase(db_session, fam["family"], name="Mittag")
    headers = headers_for(fam[who])

    listed = client.get(URL, headers=headers)
    assert listed.status_code == 200 and [p["name"] for p in listed.json()] == ["Mittag"]
    assert client.post(URL, json=body(), headers=headers).status_code == 403
    assert client.post(f"{URL}/defaults", headers=headers).status_code == 403
    assert client.patch(f"{URL}/{phase.id}", json={"name": "X"}, headers=headers).status_code == 403
    assert client.delete(f"{URL}/{phase.id}", headers=headers).status_code == 403
    db_session.expire_all()
    assert db_session.get(DayPhase, phase.id).name == "Mittag"
    assert db_session.scalars(select(DayPhase)).all() == [phase]


@pytest.mark.parametrize("who", ["papa", "mama"])
def test_both_parent_roles_can_write(client, fam, who):
    headers = headers_for(fam[who])

    created = client.post(URL, json=body(), headers=headers)
    assert created.status_code == 201
    phase_id = created.json()["id"]
    assert client.patch(f"{URL}/{phase_id}", json={"name": "Neu"}, headers=headers).json()["name"] == "Neu"
    assert client.delete(f"{URL}/{phase_id}", headers=headers).status_code == 204


# --------------------------------------------------------------------------------------------- CRUD


def test_create_returns_the_contract_shape(client, fam):
    response = client.post(URL, json=body(name="  Vor  der Schule "), headers=headers_for(fam["papa"]))

    assert response.status_code == 201
    data = response.json()
    assert set(data) == {"id", "name", "icon", "start_time", "end_time", "day_type", "sort_order"}
    assert data["name"] == "Vor der Schule"
    assert (data["icon"], data["start_time"], data["end_time"]) == ("🌅", "06:00:00", "08:00:00")
    assert (data["day_type"], data["sort_order"]) == ("school", 0)
    uuid.UUID(data["id"])


def test_create_icon_and_sort_order_are_optional(client, fam):
    payload = {"name": "Abend", "start_time": "18:00:00", "end_time": "21:00:00", "day_type": "both"}

    data = client.post(URL, json=payload, headers=headers_for(fam["papa"])).json()

    assert data["icon"] is None and data["sort_order"] == 0


def test_list_is_ordered_by_start_then_sort_order_then_name(client, db_session, fam):
    family = fam["family"]
    add_phase(db_session, family, name="Spät", start=(18, 0), end=(21, 0))
    add_phase(db_session, family, name="B", start=(7, 0), end=(8, 0), sort_order=2)
    add_phase(db_session, family, name="Z", start=(7, 0), end=(8, 0), sort_order=1)
    add_phase(db_session, family, name="A", start=(7, 0), end=(8, 0), sort_order=1)

    names = [p["name"] for p in client.get(URL, headers=headers_for(fam["mia"])).json()]

    assert names == ["A", "Z", "B", "Spät"]


def test_patch_updates_fields_and_keeps_the_rest(client, db_session, fam):
    phase = add_phase(db_session, fam["family"], name="Mittag", icon="🍽️", day_type="both")

    data = client.patch(
        f"{URL}/{phase.id}",
        json={"name": "Znüni", "end_time": "14:00", "day_type": "free", "sort_order": 3},
        headers=headers_for(fam["papa"]),
    ).json()

    assert (data["name"], data["end_time"], data["day_type"], data["sort_order"]) == ("Znüni", "14:00:00", "free", 3)
    assert data["start_time"] == "11:30:00" and data["icon"] == "🍽️"


def test_patch_icon_null_clears_the_icon(client, db_session, fam):
    phase = add_phase(db_session, fam["family"], icon="🍽️")

    data = client.patch(f"{URL}/{phase.id}", json={"icon": None}, headers=headers_for(fam["papa"])).json()

    assert data["icon"] is None


def test_delete_removes_the_phase(client, db_session, fam):
    phase = add_phase(db_session, fam["family"])
    phase_id = phase.id

    assert client.delete(f"{URL}/{phase_id}", headers=headers_for(fam["papa"])).status_code == 204

    db_session.expire_all()
    assert db_session.get(DayPhase, phase_id) is None
    assert client.delete(f"{URL}/{phase_id}", headers=headers_for(fam["papa"])).status_code == 404


# ------------------------------------------------------------------------------------------ tenancy


def test_another_family_cannot_read_change_or_delete_a_phase(client, db_session, fam, other):
    phase = add_phase(db_session, fam["family"], name="Mittag")
    attacker = headers_for(other["papa"])

    assert client.get(URL, headers=attacker).json() == []
    assert client.get(URL, headers=headers_for(other["mia"])).json() == []
    assert client.patch(f"{URL}/{phase.id}", json={"name": "Hack"}, headers=attacker).status_code == 404
    assert client.delete(f"{URL}/{phase.id}", headers=attacker).status_code == 404
    db_session.expire_all()
    assert db_session.get(DayPhase, phase.id).name == "Mittag"


def test_unknown_phase_id_is_404_and_a_malformed_one_422(client, fam):
    headers = headers_for(fam["papa"])
    assert client.patch(f"{URL}/{uuid.uuid4()}", json={"name": "X"}, headers=headers).status_code == 404
    assert client.delete(f"{URL}/{uuid.uuid4()}", headers=headers).status_code == 404
    assert client.patch(f"{URL}/nope", json={"name": "X"}, headers=headers).status_code == 422
    assert client.delete(f"{URL}/nope", headers=headers).status_code == 422


def test_each_family_only_sees_its_own_phases(client, db_session, fam, other):
    add_phase(db_session, fam["family"], name="Unsere")
    add_phase(db_session, other["family"], name="Fremde")

    assert [p["name"] for p in client.get(URL, headers=headers_for(fam["papa"])).json()] == ["Unsere"]
    assert [p["name"] for p in client.get(URL, headers=headers_for(other["papa"])).json()] == ["Fremde"]


# ---------------------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "overrides",
    [
        {"end_time": "06:00"},  # end == start
        {"end_time": "05:59"},  # end before start
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 41},
        {"day_type": "weekend"},
        {"start_time": "25:00"},
        {"start_time": "morgens"},
        {"icon": "123456789"},
        {"sort_order": -1},
        {"start_time": "06:00+02:00", "end_time": "08:00+02:00"},
    ],
)
def test_create_validation_returns_422(client, db_session, fam, overrides):
    response = client.post(URL, json=body(**overrides), headers=headers_for(fam["papa"]))

    assert response.status_code == 422
    assert db_session.scalars(select(DayPhase)).all() == []


def test_a_name_of_exactly_40_characters_is_allowed(client, fam):
    assert client.post(URL, json=body(name="x" * 40), headers=headers_for(fam["papa"])).status_code == 201


def test_missing_fields_are_422(client, fam):
    assert client.post(URL, json={"name": "X"}, headers=headers_for(fam["papa"])).status_code == 422


@pytest.mark.parametrize(
    "changes",
    [
        {"name": None},
        {"name": " "},
        {"name": "x" * 41},
        {"day_type": "x"},
        {"day_type": None},
        {"start_time": None},
        {"start_time": "09:00"},  # after the stored end (08:00)
        {"end_time": "05:00"},  # before the stored start (06:00)
        {"start_time": "10:00", "end_time": "09:00"},
    ],
)
def test_patch_validation_returns_422_and_changes_nothing(client, db_session, fam, changes):
    phase = add_phase(db_session, fam["family"], name="Früh", start=(6, 0), end=(8, 0), day_type="school")

    response = client.patch(f"{URL}/{phase.id}", json=changes, headers=headers_for(fam["papa"]))

    assert response.status_code == 422
    db_session.expire_all()
    stored = db_session.get(DayPhase, phase.id)
    assert (stored.name, stored.start_time, stored.end_time, stored.day_type) == (
        "Früh", time(6, 0), time(8, 0), "school",
    )


# ---------------------------------------------------------------------------------------- defaults


def test_defaults_seed_the_standard_set(client, db_session, fam):
    response = client.post(f"{URL}/defaults", headers=headers_for(fam["papa"]))

    assert response.status_code == 201
    rows = response.json()
    summary = [(r["name"], r["icon"], r["start_time"], r["end_time"], r["day_type"]) for r in rows]
    assert summary == [
        ("Vor der Schule", "🌅", "06:00:00", "08:00:00", "school"),
        ("Morgen", "☀️", "07:00:00", "11:30:00", "free"),
        ("Mittag", "🍽️", "11:30:00", "13:30:00", "both"),
        ("Nachmittag", "🎒", "13:30:00", "18:00:00", "both"),
        ("Vor dem Zubettgehen", "🌙", "18:00:00", "21:30:00", "both"),
    ]
    assert sorted(r["sort_order"] for r in rows) == [0, 1, 2, 3, 4]
    stored = {p.name: p.sort_order for p in db_session.scalars(select(DayPhase))}
    assert stored == {"Vor der Schule": 0, "Morgen": 1, "Mittag": 2, "Nachmittag": 3, "Vor dem Zubettgehen": 4}
    # the list endpoint agrees (ordered by start time, so the free-day "Morgen" comes after the 06:00 phase)
    listed = [p["name"] for p in client.get(URL, headers=headers_for(fam["mia"])).json()]
    assert listed == ["Vor der Schule", "Morgen", "Mittag", "Nachmittag", "Vor dem Zubettgehen"]


def test_defaults_are_refused_when_the_family_already_has_phases(client, db_session, fam):
    add_phase(db_session, fam["family"], name="Eigene")
    headers = headers_for(fam["papa"])

    response = client.post(f"{URL}/defaults", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "Es gibt schon Tagesphasen."
    assert [p.name for p in db_session.scalars(select(DayPhase))] == ["Eigene"]


def test_defaults_twice_gives_409_and_no_duplicates(client, db_session, fam):
    headers = headers_for(fam["papa"])
    assert client.post(f"{URL}/defaults", headers=headers).status_code == 201

    assert client.post(f"{URL}/defaults", headers=headers).status_code == 409

    assert len(db_session.scalars(select(DayPhase)).all()) == 5


def test_defaults_depend_only_on_the_own_family(client, db_session, fam, other):
    add_phase(db_session, other["family"], name="Fremd")

    assert client.post(f"{URL}/defaults", headers=headers_for(fam["papa"])).status_code == 201

    own = db_session.scalars(select(DayPhase).where(DayPhase.family_id == fam["family"].id)).all()
    assert len(own) == 5


# --------------------------------------------------------------------------------- delete behaviour


def test_delete_removes_open_instances_and_detaches_the_finished_ones(client, db_session, fam):
    family, mia = fam["family"], fam["mia"]
    gone = add_phase(db_session, family, name="Weg", start=(6, 0), end=(8, 0))
    kept = add_phase(db_session, family, name="Bleibt", start=(12, 0), end=(13, 0))
    template = add_template(db_session, family, phases=[gone, kept])
    assignment = add_assignment(db_session, family, mia, template)
    open_ = add_instance(db_session, template, mia, MON, assignment=assignment, phase=gone)
    kw = {"assignment": assignment, "phase": gone}
    completed = add_instance(db_session, template, mia, MON, status=TaskStatus.COMPLETED, **kw)
    approved = add_instance(db_session, template, mia, MON, status=TaskStatus.APPROVED, **kw)
    expired = add_instance(db_session, template, mia, MON, status=TaskStatus.EXPIRED, **kw)
    other_phase = add_instance(db_session, template, mia, MON, assignment=assignment, phase=kept)
    plain = add_instance(db_session, template, mia, MON)
    ids = {name: row.id for name, row in dict(
        open_=open_, completed=completed, approved=approved, expired=expired, other=other_phase, plain=plain
    ).items()}

    assert client.delete(f"{URL}/{gone.id}", headers=headers_for(fam["papa"])).status_code == 204

    db_session.expire_all()
    assert db_session.get(TaskInstance, ids["open_"]) is None
    for name in ("completed", "approved", "expired"):
        row = db_session.get(TaskInstance, ids[name])
        assert row is not None and row.phase_id is None
    assert db_session.get(TaskInstance, ids["completed"]).status == TaskStatus.COMPLETED
    assert db_session.get(TaskInstance, ids["approved"]).status == TaskStatus.APPROVED
    assert db_session.get(TaskInstance, ids["expired"]).status == TaskStatus.EXPIRED
    assert db_session.get(TaskInstance, ids["other"]).phase_id == kept.id
    assert db_session.get(TaskInstance, ids["plain"]) is not None


def test_delete_removes_the_template_links_but_not_the_template(client, db_session, fam):
    family = fam["family"]
    gone = add_phase(db_session, family, name="Weg")
    kept = add_phase(db_session, family, name="Bleibt", start=(14, 0), end=(15, 0))
    template = add_template(db_session, family, phases=[gone, kept])

    assert client.delete(f"{URL}/{gone.id}", headers=headers_for(fam["papa"])).status_code == 204

    db_session.expire_all()
    links = db_session.execute(select(task_template_phase.c.phase_id)).scalars().all()
    assert links == [kept.id]
    assert [p.id for p in template.phases] == [kept.id]


def test_delete_in_another_family_keeps_the_instances(client, db_session, fam, other):
    phase = add_phase(db_session, fam["family"])
    template = add_template(db_session, fam["family"], phases=[phase])
    instance = add_instance(db_session, template, fam["mia"], MON, phase=phase)

    assert client.delete(f"{URL}/{phase.id}", headers=headers_for(other["papa"])).status_code == 404

    db_session.expire_all()
    assert db_session.get(TaskInstance, instance.id).phase_id == phase.id


def test_template_without_phase_left_continues_as_plain_recurring_task(client, db_session, fam, monkeypatch):
    from tests.phase_helpers import freeze_clock

    freeze_clock(monkeypatch)
    family, mia = fam["family"], fam["mia"]
    only = add_phase(db_session, family, name="Einzige", day_type="both")
    template = add_template(db_session, family, phases=[only])
    assignment = add_assignment(db_session, family, mia, template)
    add_timetable(db_session, family, mia)
    from app.core.task_recurrence import generate_instances_for_assignment

    generate_instances_for_assignment(db_session, assignment)
    assert {i.phase_id for i in db_session.scalars(select(TaskInstance))} == {only.id}

    assert client.delete(f"{URL}/{only.id}", headers=headers_for(fam["papa"])).status_code == 204

    db_session.expire_all()
    rows = db_session.scalars(select(TaskInstance)).all()
    assert rows and {i.phase_id for i in rows} == {None}
