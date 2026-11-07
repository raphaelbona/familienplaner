"""Task templates with day phases: template API, day type, generation per phase, one-off tasks, reconcile after
school breaks / phase changes, the worker job and the phase start gating."""

import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.clock import local_timezone
from app.core.day_type import day_type_for, day_types_for_range, phase_applies
from app.core.task_recurrence import (
    generate_instances_for_assignment,
    prune_mismatched_instances,
    reconcile_family_task_instances,
)
from app.models.school_break import SchoolBreak
from app.models.task import TaskAssignment, TaskInstance, TaskStatus
from tests.google_helpers import FakeGoogle, all_day, headers_for, make_account, make_family, sync
from tests.phase_helpers import (
    MON,
    add_assignment,
    add_instance,
    add_phase,
    add_template,
    add_timetable,
    by_day,
    d,
    freeze_clock,
    instances_of,
)

TEMPLATES = "/tasks/templates"
INSTANCES = "/tasks/instances"


@pytest.fixture()
def fam(db_session):
    return make_family(db_session)


@pytest.fixture()
def other(db_session):
    return make_family(db_session, name="Andere", tag="b")


@pytest.fixture()
def frozen(monkeypatch):
    freeze_clock(monkeypatch)


@pytest.fixture()
def phases(db_session, fam):
    """School-only, free-only and both-days phase of the family."""
    family = fam["family"]
    return {
        "school": add_phase(db_session, family, name="Vor der Schule", start=(6, 0), end=(8, 0), day_type="school"),
        "free": add_phase(db_session, family, name="Morgen", start=(7, 0), end=(11, 30), day_type="free"),
        "both": add_phase(db_session, family, name="Mittag", start=(11, 30), end=(13, 30), day_type="both"),
    }


def template_body(**extra) -> dict:
    return {"title": "Duschen", "kind": "basis", **extra}


def post_template(client, headers, **extra):
    return client.post(TEMPLATES, json=template_body(**extra), headers=headers)


# ------------------------------------------------------------------------------------- template API


def test_template_defaults_have_no_phases_and_no_carry_over(client, fam):
    data = post_template(client, headers_for(fam["papa"])).json()

    assert data["phase_ids"] == [] and data["carry_over"] is False


def test_template_create_with_phases_and_carry_over(client, fam, phases):
    ids = [str(phases["school"].id), str(phases["both"].id)]

    response = post_template(client, headers_for(fam["papa"]), phase_ids=ids, carry_over=True)

    assert response.status_code == 201
    data = response.json()
    assert data["phase_ids"] == ids and data["carry_over"] is True
    listed = client.get(TEMPLATES, headers=headers_for(fam["mia"])).json()
    assert listed[0]["phase_ids"] == ids and listed[0]["carry_over"] is True


def test_template_phase_ids_are_deduplicated(client, fam, phases):
    phase_id = str(phases["both"].id)

    data = post_template(client, headers_for(fam["papa"]), phase_ids=[phase_id, phase_id]).json()

    assert data["phase_ids"] == [phase_id]


def test_template_with_a_foreign_or_unknown_phase_is_404_and_nothing_is_created(client, db_session, fam, other):
    foreign = add_phase(db_session, other["family"], name="Fremd")
    own = add_phase(db_session, fam["family"], name="Eigen")
    headers = headers_for(fam["papa"])

    for bad in (str(foreign.id), str(uuid.uuid4())):
        response = post_template(client, headers, phase_ids=[str(own.id), bad])
        assert response.status_code == 404
        assert response.json()["detail"] == "Tagesphase nicht gefunden"
    assert client.get(TEMPLATES, headers=headers).json() == []


def test_template_phase_ids_must_be_uuids_and_are_capped(client, fam):
    headers = headers_for(fam["papa"])
    assert post_template(client, headers, phase_ids=["x"]).status_code == 422
    assert post_template(client, headers, phase_ids=[str(uuid.uuid4()) for _ in range(21)]).status_code == 422


def test_template_update_replaces_the_phase_links(client, fam, phases):
    headers = headers_for(fam["papa"])
    template = post_template(client, headers, phase_ids=[str(phases["school"].id)]).json()

    updated = client.patch(
        f"{TEMPLATES}/{template['id']}", json={"phase_ids": [str(phases["free"].id), str(phases["both"].id)]},
        headers=headers,
    ).json()

    assert updated["phase_ids"] == [str(phases["free"].id), str(phases["both"].id)]


def test_template_update_without_phase_ids_keeps_them_and_empty_list_clears_them(client, fam, phases):
    headers = headers_for(fam["papa"])
    ids = [str(phases["school"].id)]
    template = post_template(client, headers, phase_ids=ids).json()

    renamed = client.patch(f"{TEMPLATES}/{template['id']}", json={"title": "Baden"}, headers=headers).json()
    assert renamed["phase_ids"] == ids

    cleared = client.patch(f"{TEMPLATES}/{template['id']}", json={"phase_ids": []}, headers=headers).json()
    assert cleared["phase_ids"] == []


def test_template_update_toggles_carry_over(client, fam):
    headers = headers_for(fam["papa"])
    template = post_template(client, headers).json()

    on = client.patch(f"{TEMPLATES}/{template['id']}", json={"carry_over": True}, headers=headers).json()
    off = client.patch(f"{TEMPLATES}/{template['id']}", json={"carry_over": False}, headers=headers).json()

    assert on["carry_over"] is True and off["carry_over"] is False
    assert client.patch(f"{TEMPLATES}/{template['id']}", json={"carry_over": None}, headers=headers).status_code == 422


def test_template_update_with_a_foreign_phase_is_404_and_changes_nothing(client, db_session, fam, other):
    foreign = add_phase(db_session, other["family"], name="Fremd")
    own = add_phase(db_session, fam["family"], name="Eigen")
    headers = headers_for(fam["papa"])
    template = post_template(client, headers, phase_ids=[str(own.id)]).json()

    response = client.patch(
        f"{TEMPLATES}/{template['id']}", json={"title": "Neu", "phase_ids": [str(foreign.id)]}, headers=headers
    )

    assert response.status_code == 404
    again = client.get(TEMPLATES, headers=headers).json()[0]
    assert again["phase_ids"] == [str(own.id)] and again["title"] == "Duschen"


def test_instance_read_has_phase_id_and_the_template_phase_ids(client, db_session, fam, phases):
    template = add_template(db_session, fam["family"], phases=[phases["both"]])
    add_instance(db_session, template, fam["mia"], MON, phase=phases["both"])
    add_instance(db_session, template, fam["mia"], d(1))

    rows = client.get(INSTANCES, headers=headers_for(fam["mia"])).json()

    assert [r["phase_id"] for r in rows] == [str(phases["both"].id), None]
    assert rows[0]["template"]["phase_ids"] == [str(phases["both"].id)]
    assert rows[0]["template"]["carry_over"] is False


# ------------------------------------------------------------------------------------------ day type


def test_day_type_follows_the_timetable_weekdays(db_session, fam):
    add_timetable(db_session, fam["family"], fam["mia"], weekdays=(0, 2))
    fid, mia, leo = fam["family"].id, fam["mia"].id, fam["leo"].id

    assert day_type_for(db_session, mia, fid, d(0)) == "school"  # Monday
    assert day_type_for(db_session, mia, fid, d(1)) == "free"  # Tuesday: no lesson
    assert day_type_for(db_session, mia, fid, d(2)) == "school"  # Wednesday
    assert day_type_for(db_session, mia, fid, d(5)) == "free"  # Saturday
    assert day_type_for(db_session, leo, fid, d(0)) == "free"  # no timetable at all


def test_day_type_is_free_on_a_break_of_this_child_only(db_session, fam):
    add_timetable(db_session, fam["family"], fam["mia"])
    add_timetable(db_session, fam["family"], fam["leo"])
    db_session.add(
        SchoolBreak(
            family_id=fam["family"].id, title="Ferien", kind="ferien", start_date=d(1), end_date=d(2),
            child_user_ids=[fam["mia"].id],
        )
    )
    db_session.add(
        SchoolBreak(
            family_id=fam["family"].id, title="Frei", kind="schulfrei", start_date=d(3), end_date=d(3),
            child_user_ids=[],
        )
    )
    db_session.commit()
    fid, mia, leo = fam["family"].id, fam["mia"].id, fam["leo"].id

    assert [day_type_for(db_session, mia, fid, d(i)) for i in range(5)] == [
        "school", "free", "free", "free", "school",
    ]
    assert [day_type_for(db_session, leo, fid, d(i)) for i in range(5)] == [
        "school", "school", "school", "free", "school",
    ]


def test_day_type_ignores_timetables_and_breaks_of_other_families(db_session, fam, other):
    add_timetable(db_session, other["family"], other["mia"])
    db_session.add(SchoolBreak(family_id=other["family"].id, title="F", kind="ferien", start_date=d(0), end_date=d(9)))
    db_session.commit()
    add_timetable(db_session, fam["family"], fam["mia"])

    assert day_type_for(db_session, fam["mia"].id, fam["family"].id, d(0)) == "school"
    assert day_type_for(db_session, other["mia"].id, fam["family"].id, d(0)) == "free"


def test_day_types_for_range_matches_the_single_lookup(db_session, fam):
    add_timetable(db_session, fam["family"], fam["mia"])
    db_session.add(SchoolBreak(family_id=fam["family"].id, title="F", kind="ferien", start_date=d(2), end_date=d(3)))
    db_session.commit()
    fid, mia = fam["family"].id, fam["mia"].id

    batch = day_types_for_range(db_session, mia, fid, d(0), d(13))

    assert len(batch) == 14
    assert batch == {d(i): day_type_for(db_session, mia, fid, d(i)) for i in range(14)}
    assert batch[d(5)] == "free" and batch[d(2)] == "free" and batch[d(7)] == "school"


def test_a_parent_account_is_always_on_a_free_day(db_session, fam):
    add_timetable(db_session, fam["family"], fam["mia"])
    assert day_type_for(db_session, fam["papa"].id, fam["family"].id, d(0)) == "free"


def test_phase_applies(db_session, fam, phases):
    assert phase_applies(phases["school"], "school") and not phase_applies(phases["school"], "free")
    assert phase_applies(phases["free"], "free") and not phase_applies(phases["free"], "school")
    assert phase_applies(phases["both"], "school") and phase_applies(phases["both"], "free")


# ------------------------------------------------------------------------------------- generation


def make_daily(db_session, fam, template_phases, *, weekdays=(0, 1, 2, 3, 4), rule="daily", days=None):
    add_timetable(db_session, fam["family"], fam["mia"], weekdays=weekdays)
    template = add_template(db_session, fam["family"], phases=template_phases)
    assignment = add_assignment(db_session, fam["family"], fam["mia"], template, rule=rule, weekdays=days)
    return template, assignment


def test_daily_template_gets_one_instance_per_applicable_phase(db_session, fam, phases):
    _, assignment = make_daily(db_session, fam, [phases["school"], phases["free"], phases["both"]])

    created = generate_instances_for_assignment(db_session, assignment, today=MON)

    school, free, both = (phases[k].id for k in ("school", "free", "both"))
    days = by_day(created)
    assert len(created) == sum(len(v) for v in days.values())
    for offset in range(15):
        expected = {school, both} if (MON + timedelta(days=offset)).weekday() < 5 else {free, both}
        assert days[d(offset)] == expected, d(offset)
    assert all(i.assignment_id == assignment.id for i in created)


def test_a_template_with_only_a_school_phase_skips_the_free_days(db_session, fam, phases):
    _, assignment = make_daily(db_session, fam, [phases["school"]])

    created = generate_instances_for_assignment(db_session, assignment, today=MON)

    assert {i.due_date.weekday() for i in created} == {0, 1, 2, 3, 4}
    assert all(i.phase_id == phases["school"].id for i in created)
    assert len(created) == 11  # Mon..Fri + Mon..Fri + Mon of the 15 day window


def test_a_template_without_phases_stays_one_instance_per_day(db_session, fam):
    _, assignment = make_daily(db_session, fam, [])

    created = generate_instances_for_assignment(db_session, assignment, today=MON)

    assert len(created) == 15
    assert {i.phase_id for i in created} == {None}


def test_generation_is_idempotent_per_date_and_phase(db_session, fam, phases):
    _, assignment = make_daily(db_session, fam, [phases["school"], phases["both"]])
    first = generate_instances_for_assignment(db_session, assignment, today=MON)

    again = generate_instances_for_assignment(db_session, assignment, today=MON)
    later = generate_instances_for_assignment(db_session, assignment, today=d(3))

    assert first and again == []
    assert {(i.due_date, i.phase_id) for i in later}.isdisjoint({(i.due_date, i.phase_id) for i in first})
    all_rows = instances_of(db_session, assignment)
    assert len({(i.due_date, i.phase_id) for i in all_rows}) == len(all_rows)


def test_a_missing_phase_instance_is_topped_up_without_duplicating_the_others(db_session, fam, phases):
    _, assignment = make_daily(db_session, fam, [phases["school"], phases["both"]])
    generate_instances_for_assignment(db_session, assignment, today=MON)
    victim = next(
        i for i in instances_of(db_session, assignment) if i.due_date == d(1) and i.phase_id == phases["both"].id
    )
    db_session.delete(victim)
    db_session.commit()

    created = generate_instances_for_assignment(db_session, assignment, today=MON)

    assert [(i.due_date, i.phase_id) for i in created] == [(d(1), phases["both"].id)]


def test_weekly_template_with_phases_uses_the_day_type_of_each_weekday(db_session, fam, phases):
    # Monday (school) and Saturday (free)
    _, assignment = make_daily(
        db_session, fam, [phases["school"], phases["free"], phases["both"]], rule="weekly", days="0,5"
    )

    created = generate_instances_for_assignment(db_session, assignment, today=MON)

    days = by_day(created)
    for day, kinds in days.items():
        expected = {phases["both"].id, phases["school"].id if day.weekday() == 0 else phases["free"].id}
        assert kinds == expected, day


def test_the_child_s_own_day_type_decides(db_session, fam, phases):
    add_timetable(db_session, fam["family"], fam["mia"])  # Leo has no timetable
    template = add_template(db_session, fam["family"], phases=[phases["school"], phases["free"]])
    mia = add_assignment(db_session, fam["family"], fam["mia"], template)
    leo = add_assignment(db_session, fam["family"], fam["leo"], template)

    generate_instances_for_assignment(db_session, mia, today=MON)
    generate_instances_for_assignment(db_session, leo, today=MON)

    assert by_day(instances_of(db_session, mia))[d(0)] == {phases["school"].id}
    assert by_day(instances_of(db_session, leo))[d(0)] == {phases["free"].id}


def test_a_school_break_makes_the_days_free_in_the_generation(db_session, fam, phases):
    db_session.add(SchoolBreak(family_id=fam["family"].id, title="F", kind="ferien", start_date=d(1), end_date=d(2)))
    db_session.commit()
    _, assignment = make_daily(db_session, fam, [phases["school"], phases["free"]])

    created = generate_instances_for_assignment(db_session, assignment, today=MON)

    days = by_day(created)
    assert days[d(0)] == {phases["school"].id}
    assert days[d(1)] == {phases["free"].id} and days[d(2)] == {phases["free"].id}
    assert days[d(3)] == {phases["school"].id}


# ----------------------------------------------------------------------------------- one-off tasks


def create_one_off(client, headers, template_id, child, due, **extra):
    return client.post(
        INSTANCES,
        json={"template_id": str(template_id), "assignee_user_id": str(child.id), "due_date": due.isoformat(), **extra},
        headers=headers,
    )


def stored(db_session, template_id):
    db_session.expire_all()
    return list(db_session.scalars(select(TaskInstance).where(TaskInstance.template_id == template_id)))


def test_one_off_task_is_split_into_the_phases_of_that_day(client, db_session, fam, phases):
    add_timetable(db_session, fam["family"], fam["mia"])
    template = add_template(db_session, fam["family"], phases=[phases["school"], phases["free"], phases["both"]])
    headers = headers_for(fam["papa"])

    monday = create_one_off(client, headers, template.id, fam["mia"], d(0))
    saturday = create_one_off(client, headers, template.id, fam["mia"], d(5))

    assert monday.status_code == 201 and saturday.status_code == 201
    # the response is the first created instance (phases are ordered by start time)
    assert monday.json()["phase_id"] == str(phases["school"].id)
    assert saturday.json()["phase_id"] == str(phases["free"].id)
    days = by_day(stored(db_session, template.id))
    assert days[d(0)] == {phases["school"].id, phases["both"].id}
    assert days[d(5)] == {phases["free"].id, phases["both"].id}
    assert all(i.assignment_id is None for i in stored(db_session, template.id))


def test_one_off_task_without_an_applicable_phase_becomes_a_single_plain_instance(client, db_session, fam, phases):
    add_timetable(db_session, fam["family"], fam["mia"])
    template = add_template(db_session, fam["family"], phases=[phases["school"]])

    response = create_one_off(client, headers_for(fam["papa"]), template.id, fam["mia"], d(5))  # Saturday

    assert response.status_code == 201 and response.json()["phase_id"] is None
    assert [i.phase_id for i in stored(db_session, template.id)] == [None]


def test_one_off_task_without_phases_is_unchanged(client, db_session, fam):
    template = add_template(db_session, fam["family"])

    response = create_one_off(client, headers_for(fam["papa"]), template.id, fam["mia"], d(0))

    assert response.status_code == 201 and response.json()["phase_id"] is None
    assert len(stored(db_session, template.id)) == 1


def test_one_off_task_for_two_children_uses_each_day_type(client, db_session, fam, phases):
    add_timetable(db_session, fam["family"], fam["mia"])  # Leo: free every day
    template = add_template(db_session, fam["family"], phases=[phases["school"], phases["free"]])

    response = client.post(
        INSTANCES,
        json={
            "template_id": str(template.id),
            "assignee_user_ids": [str(fam["mia"].id), str(fam["leo"].id)],
            "due_date": d(0).isoformat(),
        },
        headers=headers_for(fam["papa"]),
    )

    assert response.status_code == 201
    rows = {(i.assignee_user_id, i.phase_id) for i in stored(db_session, template.id)}
    assert rows == {(fam["mia"].id, phases["school"].id), (fam["leo"].id, phases["free"].id)}


def test_recurring_assignment_with_phases_via_the_api(client, db_session, fam, phases, frozen):
    add_timetable(db_session, fam["family"], fam["mia"])
    template = add_template(db_session, fam["family"], phases=[phases["school"], phases["both"]])

    response = create_one_off(
        client, headers_for(fam["papa"]), template.id, fam["mia"], d(0), recurrence_rule="daily"
    )

    assert response.status_code == 201
    assert response.json()["phase_id"] == str(phases["school"].id) and response.json()["due_date"] == d(0).isoformat()
    assignment = db_session.scalar(select(TaskAssignment))
    days = by_day(instances_of(db_session, assignment))
    assert days[d(0)] == {phases["school"].id, phases["both"].id}
    assert days[d(5)] == {phases["both"].id}  # Saturday: the school phase does not exist


def test_recurring_assignment_without_any_matching_day_is_rejected(client, db_session, fam, phases, frozen):
    template = add_template(db_session, fam["family"], phases=[phases["school"]])  # Mia has no school days

    response = create_one_off(
        client, headers_for(fam["papa"]), template.id, fam["mia"], d(0), recurrence_rule="daily"
    )

    assert response.status_code == 422
    assert db_session.scalars(select(TaskAssignment)).all() == []
    assert stored(db_session, template.id) == []


# ----------------------------------------------------------------------------------------- reconcile


def week_setup(db_session, fam, phases, template_phases):
    template, assignment = make_daily(db_session, fam, template_phases)
    generate_instances_for_assignment(db_session, assignment, today=MON)
    return template, assignment


def post_break(client, fam, first, last, **extra):
    return client.post(
        "/school-breaks",
        json={"title": "Ferien", "start_date": first.isoformat(), "end_date": last.isoformat(), **extra},
        headers=headers_for(fam["papa"]),
    )


def test_a_new_school_break_swaps_the_phase_instances_of_those_days(client, db_session, fam, phases, frozen):
    _, assignment = week_setup(db_session, fam, phases, [phases["school"], phases["free"], phases["both"]])
    school, free, both = (phases[k].id for k in ("school", "free", "both"))
    assert by_day(instances_of(db_session, assignment))[d(2)] == {school, both}

    assert post_break(client, fam, d(2), d(3)).status_code == 201

    days = by_day(instances_of(db_session, assignment))
    assert days[d(1)] == {school, both} and days[d(4)] == {school, both}  # untouched
    assert days[d(2)] == {free, both} and days[d(3)] == {free, both}  # became free days


def test_deleting_the_break_swaps_the_instances_back(client, db_session, fam, phases, frozen):
    _, assignment = week_setup(db_session, fam, phases, [phases["school"], phases["free"]])
    school, free = phases["school"].id, phases["free"].id
    break_id = post_break(client, fam, d(2), d(3)).json()["id"]
    assert by_day(instances_of(db_session, assignment))[d(2)] == {free}

    assert client.delete(f"/school-breaks/{break_id}", headers=headers_for(fam["papa"])).status_code == 204

    assert by_day(instances_of(db_session, assignment))[d(2)] == {school}


def test_changing_the_break_range_reconciles_again(client, db_session, fam, phases, frozen):
    _, assignment = week_setup(db_session, fam, phases, [phases["school"], phases["free"]])
    school, free = phases["school"].id, phases["free"].id
    break_id = post_break(client, fam, d(1), d(3)).json()["id"]

    patched = client.patch(
        f"/school-breaks/{break_id}", json={"end_date": d(1).isoformat()}, headers=headers_for(fam["papa"])
    )

    assert patched.status_code == 200
    days = by_day(instances_of(db_session, assignment))
    assert days[d(1)] == {free} and days[d(2)] == {school} and days[d(3)] == {school}


def test_a_break_for_one_child_only_changes_that_child(client, db_session, fam, phases, frozen):
    add_timetable(db_session, fam["family"], fam["mia"])
    add_timetable(db_session, fam["family"], fam["leo"])
    template = add_template(db_session, fam["family"], phases=[phases["school"], phases["free"]])
    mia = add_assignment(db_session, fam["family"], fam["mia"], template)
    leo = add_assignment(db_session, fam["family"], fam["leo"], template)
    for assignment in (mia, leo):
        generate_instances_for_assignment(db_session, assignment, today=MON)

    post_break(client, fam, d(1), d(1), child_user_ids=[str(fam["mia"].id)])

    assert by_day(instances_of(db_session, mia))[d(1)] == {phases["free"].id}
    assert by_day(instances_of(db_session, leo))[d(1)] == {phases["school"].id}


def test_reconcile_never_touches_done_expired_past_and_one_off_instances(db_session, fam, phases):
    school, free = phases["school"], phases["free"]
    template, assignment = make_daily(db_session, fam, [school, free])
    # Wednesday (index 2) will become a free day: its school instances must go, unless they are done.
    db_session.add(SchoolBreak(family_id=fam["family"].id, title="F", kind="ferien", start_date=d(0), end_date=d(4)))
    db_session.commit()
    mia = fam["mia"]
    kw = {"assignment": assignment, "phase": school}
    done = add_instance(db_session, template, mia, d(2), status=TaskStatus.COMPLETED, **kw)
    approved = add_instance(db_session, template, mia, d(3), status=TaskStatus.APPROVED, **kw)
    expired = add_instance(db_session, template, mia, d(4), status=TaskStatus.EXPIRED, **kw)
    past = add_instance(db_session, template, mia, d(0), assignment=assignment, phase=school)
    open_stale = add_instance(db_session, template, mia, d(1), assignment=assignment, phase=school)
    one_off = add_instance(db_session, template, mia, d(1), phase=school)
    past_id, stale_id = past.id, open_stale.id

    reconcile_family_task_instances(db_session, fam["family"].id, today=d(1))

    db_session.expire_all()
    for row in (done, approved, expired, one_off):
        assert db_session.get(TaskInstance, row.id) is not None
    assert db_session.get(TaskInstance, done.id).status == TaskStatus.COMPLETED
    assert db_session.get(TaskInstance, past_id) is not None  # before today
    assert db_session.get(TaskInstance, stale_id) is None  # open, today, wrong phase now
    days = by_day(instances_of(db_session, assignment))
    assert free.id in days[d(1)] and school.id not in days[d(1)]


def test_reconcile_is_idempotent(db_session, fam, phases):
    _, assignment = week_setup(db_session, fam, phases, [phases["school"], phases["free"]])
    before = sorted((i.due_date, str(i.phase_id)) for i in instances_of(db_session, assignment))

    reconcile_family_task_instances(db_session, fam["family"].id, today=MON)
    reconcile_family_task_instances(db_session, fam["family"].id, today=MON)

    assert sorted((i.due_date, str(i.phase_id)) for i in instances_of(db_session, assignment)) == before


def test_reconcile_ignores_other_families_and_archived_templates(db_session, fam, other, phases):
    template, assignment = make_daily(db_session, fam, [phases["school"]])
    generate_instances_for_assignment(db_session, assignment, today=MON)
    foreign_phase = add_phase(db_session, other["family"], name="Fremd")
    foreign_template = add_template(db_session, other["family"], phases=[foreign_phase])
    foreign = add_assignment(db_session, other["family"], other["mia"], foreign_template)
    add_instance(db_session, foreign_template, other["mia"], d(1), assignment=foreign)  # would be stale (no phase)
    template.archived_at = datetime.now(local_timezone())
    db_session.commit()
    add_instance(db_session, template, fam["mia"], d(1), assignment=assignment)  # stale, but archived

    deleted = prune_mismatched_instances(db_session, fam["family"].id, today=MON)

    assert deleted == 0
    assert len(instances_of(db_session, foreign)) == 1


def test_changing_the_template_phases_reconciles_the_future_instances(client, db_session, fam, phases, frozen):
    school, free, both = phases["school"], phases["free"], phases["both"]
    template, assignment = week_setup(db_session, fam, phases, [school])
    assert all(i.phase_id == school.id for i in instances_of(db_session, assignment))
    headers = headers_for(fam["papa"])

    moved = client.patch(f"{TEMPLATES}/{template.id}", json={"phase_ids": [str(both.id)]}, headers=headers)

    assert moved.status_code == 200
    rows = instances_of(db_session, assignment)
    assert {i.phase_id for i in rows} == {both.id}
    assert set(by_day(rows)) == {d(i) for i in range(15)}  # the free days got the new phase, too

    cleared = client.patch(f"{TEMPLATES}/{template.id}", json={"phase_ids": []}, headers=headers)

    assert cleared.status_code == 200
    rows = instances_of(db_session, assignment)
    assert {i.phase_id for i in rows} == {None} and len(rows) == 15

    both_again = client.patch(
        f"{TEMPLATES}/{template.id}", json={"phase_ids": [str(school.id), str(free.id)]}, headers=headers
    )
    assert both_again.status_code == 200
    days = by_day(instances_of(db_session, assignment))
    assert days[d(0)] == {school.id} and days[d(5)] == {free.id}


def test_a_changed_phase_day_type_reconciles(client, db_session, fam, phases, frozen):
    _, assignment = week_setup(db_session, fam, phases, [phases["school"]])
    assert d(5) not in by_day(instances_of(db_session, assignment))

    response = client.patch(
        f"/day-phases/{phases['school'].id}", json={"day_type": "both"}, headers=headers_for(fam["papa"])
    )

    assert response.status_code == 200
    assert phases["school"].id in by_day(instances_of(db_session, assignment))[d(5)]


def test_a_timetable_change_reconciles_the_phase_instances(client, db_session, fam, phases, frozen):
    _, assignment = week_setup(db_session, fam, phases, [phases["school"], phases["free"]])
    assert by_day(instances_of(db_session, assignment))[d(5)] == {phases["free"].id}

    response = client.post(
        "/timetables",
        json={
            "child_user_id": str(fam["mia"].id), "day_of_week": 5, "start_time": "09:00", "end_time": "10:00",
            "subject": "Musik",
        },
        headers=headers_for(fam["papa"]),
    )

    assert response.status_code == 201
    assert by_day(instances_of(db_session, assignment))[d(5)] == {phases["school"].id}


def test_a_google_break_import_reconciles_the_phase_instances(db_session, fam, phases, frozen):
    _, assignment = week_setup(db_session, fam, phases, [phases["school"], phases["free"]])
    assert by_day(instances_of(db_session, assignment))[d(1)] == {phases["school"].id}
    fake = FakeGoogle()
    fake.set_events("primary", [all_day("e1", "Herbstferien", d(1), d(3))])  # Tue + Wed
    account = make_account(db_session, fam["papa"])

    sync(db_session, account, fake, now=datetime(2026, 11, 2, 9, 0, tzinfo=local_timezone()))

    days = by_day(instances_of(db_session, assignment))
    assert days[d(1)] == {phases["free"].id} and days[d(2)] == {phases["free"].id}
    assert days[d(3)] == {phases["school"].id}


# ---------------------------------------------------------------------------------------- the worker


def test_worker_job_tops_up_prunes_and_expires(db_session, fam, phases, frozen, monkeypatch):
    from app.worker import sync_worker

    monkeypatch.setattr(sync_worker, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    template, assignment = make_daily(db_session, fam, [phases["school"], phases["free"]])
    db_session.add(SchoolBreak(family_id=fam["family"].id, title="F", kind="ferien", start_date=d(2), end_date=d(2)))
    stale = add_instance(db_session, template, fam["mia"], d(2), assignment=assignment, phase=phases["school"])
    yesterday = add_instance(db_session, template, fam["mia"], d(-1), assignment=assignment, phase=phases["school"])
    db_session.commit()
    stale_id, yesterday_id = stale.id, yesterday.id

    sync_worker.generate_recurring_task_instances()

    db_session.expire_all()
    assert db_session.get(TaskInstance, stale_id) is None
    days = by_day(instances_of(db_session, assignment))
    assert days[d(2)] == {phases["free"].id} and days[d(0)] == {phases["school"].id}
    assert db_session.get(TaskInstance, yesterday_id).status == TaskStatus.EXPIRED


# --------------------------------------------------------------------------------- start gating


def at(hour: int, minute: int = 0, day: date = MON) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=local_timezone())


def setup_gate(db_session, fam, *, due=MON, start=(11, 30)):
    phase = add_phase(db_session, fam["family"], name="Mittag", start=start, end=(13, 30))
    template = add_template(db_session, fam["family"], phases=[phase])
    instance = add_instance(db_session, template, fam["mia"], due, phase=phase)
    return phase, instance


def complete(client, who, instance):
    return client.post(f"{INSTANCES}/{instance.id}/complete", headers=headers_for(who))


def test_a_phase_task_cannot_be_completed_before_the_phase_starts(client, db_session, fam, monkeypatch):
    freeze_clock(monkeypatch, at(11, 29))
    _, instance = setup_gate(db_session, fam)

    response = complete(client, fam["mia"], instance)

    assert response.status_code == 400
    assert response.json()["detail"] == "Diese Phase beginnt erst um 11:30."
    db_session.expire_all()
    assert db_session.get(TaskInstance, instance.id).status == TaskStatus.OPEN


def test_a_phase_task_can_be_completed_from_the_start_of_the_phase(client, db_session, fam, monkeypatch):
    freeze_clock(monkeypatch, at(11, 30))
    _, instance = setup_gate(db_session, fam)

    response = complete(client, fam["mia"], instance)

    assert response.status_code == 200 and response.json()["status"] == "completed"


def test_the_gate_applies_to_every_role(client, db_session, fam, monkeypatch):
    freeze_clock(monkeypatch, at(7, 0))
    _, instance = setup_gate(db_session, fam)

    for who in ("mia", "papa", "mama", "wall"):
        response = complete(client, fam[who], instance)
        assert response.status_code == 400, who
        assert response.json()["detail"] == "Diese Phase beginnt erst um 11:30."


def test_past_phase_tasks_are_never_gated(client, db_session, fam, monkeypatch):
    freeze_clock(monkeypatch, at(7, 0))
    _, instance = setup_gate(db_session, fam, due=d(-1))

    assert complete(client, fam["mia"], instance).status_code == 200


def test_future_phase_tasks_keep_the_due_day_message(client, db_session, fam, monkeypatch):
    freeze_clock(monkeypatch, at(23, 0))
    _, instance = setup_gate(db_session, fam, due=d(1))

    response = complete(client, fam["mia"], instance)

    assert response.status_code == 400 and "Fälligkeitstag" in response.json()["detail"]


def test_tasks_without_a_phase_are_not_gated(client, db_session, fam, monkeypatch):
    freeze_clock(monkeypatch, at(5, 0))
    template = add_template(db_session, fam["family"])
    instance = add_instance(db_session, template, fam["mia"], MON)

    assert complete(client, fam["mia"], instance).status_code == 200
