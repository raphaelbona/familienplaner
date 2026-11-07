"""Missed recurring tasks must not pile up: expire_stale_instances, the list endpoint and the worker job, and
the 'Aufgabe ist verfallen.' guard on complete / reopen / approve."""

from datetime import date

import pytest
from sqlalchemy import select

from app.core.task_recurrence import expire_stale_instances
from app.models.task import TaskInstance, TaskStatus
from tests.google_helpers import headers_for, make_family
from tests.phase_helpers import (
    MON,
    add_assignment,
    add_instance,
    add_phase,
    add_template,
    d,
    freeze_clock,
)

INSTANCES = "/tasks/instances"
EXPIRED_MSG = "Aufgabe ist verfallen."
WED, THU, FRI, SAT, SUN = d(2), d(3), d(4), d(5), d(6)


@pytest.fixture()
def fam(db_session):
    return make_family(db_session)


@pytest.fixture()
def other(db_session):
    return make_family(db_session, name="Andere", tag="b")


def statuses(db, *rows) -> list[TaskStatus]:
    db.expire_all()
    return [db.get(TaskInstance, row.id).status for row in rows]


def weekly(db, fam, *, carry_over=False, phases=()):
    template = add_template(db, fam["family"], carry_over=carry_over, phases=phases)
    assignment = add_assignment(db, fam["family"], fam["mia"], template, rule="weekly", weekdays="2,5")
    return template, assignment


# ----------------------------------------------------------------------------------------- weekly


def test_a_missed_weekly_task_stays_doable_until_the_next_one_is_due(db_session, fam):
    template, assignment = weekly(db_session, fam)
    wed = add_instance(db_session, template, fam["mia"], WED, assignment=assignment)
    sat = add_instance(db_session, template, fam["mia"], SAT, assignment=assignment)
    fid = fam["family"].id

    for today in (WED, THU, FRI):
        assert expire_stale_instances(db_session, fid, today=today) == 0
        assert statuses(db_session, wed, sat) == [TaskStatus.OPEN, TaskStatus.OPEN]

    assert expire_stale_instances(db_session, fid, today=SAT) == 1
    assert statuses(db_session, wed, sat) == [TaskStatus.EXPIRED, TaskStatus.OPEN]

    assert expire_stale_instances(db_session, fid, today=SUN) == 0  # nothing more, Saturday is still open
    assert statuses(db_session, wed, sat) == [TaskStatus.EXPIRED, TaskStatus.OPEN]


def test_a_completed_latest_instance_still_expires_the_older_open_one(db_session, fam):
    template, assignment = weekly(db_session, fam)
    wed = add_instance(db_session, template, fam["mia"], WED, assignment=assignment)
    sat = add_instance(db_session, template, fam["mia"], SAT, assignment=assignment, status=TaskStatus.COMPLETED)

    assert expire_stale_instances(db_session, fam["family"].id, today=SAT) == 1

    assert statuses(db_session, wed, sat) == [TaskStatus.EXPIRED, TaskStatus.COMPLETED]


def test_future_instances_are_never_touched(db_session, fam):
    template, assignment = weekly(db_session, fam)
    wed = add_instance(db_session, template, fam["mia"], WED, assignment=assignment)
    sat = add_instance(db_session, template, fam["mia"], SAT, assignment=assignment)
    next_wed = add_instance(db_session, template, fam["mia"], d(9), assignment=assignment)

    assert expire_stale_instances(db_session, fam["family"].id, today=THU) == 0

    assert statuses(db_session, wed, sat, next_wed) == [TaskStatus.OPEN] * 3


# ------------------------------------------------------------------------------------------- daily


def test_yesterdays_missed_daily_task_expires_today(db_session, fam):
    template = add_template(db_session, fam["family"])
    assignment = add_assignment(db_session, fam["family"], fam["mia"], template)
    rows = [add_instance(db_session, template, fam["mia"], d(i), assignment=assignment) for i in range(-2, 3)]

    assert expire_stale_instances(db_session, fam["family"].id, today=MON) == 2

    assert statuses(db_session, *rows) == [
        TaskStatus.EXPIRED, TaskStatus.EXPIRED, TaskStatus.OPEN, TaskStatus.OPEN, TaskStatus.OPEN,
    ]


def test_done_instances_are_never_expired(db_session, fam):
    template = add_template(db_session, fam["family"])
    assignment = add_assignment(db_session, fam["family"], fam["mia"], template)
    done = add_instance(db_session, template, fam["mia"], d(-3), assignment=assignment, status=TaskStatus.COMPLETED)
    approved = add_instance(db_session, template, fam["mia"], d(-2), assignment=assignment, status=TaskStatus.APPROVED)
    today = add_instance(db_session, template, fam["mia"], MON, assignment=assignment)

    assert expire_stale_instances(db_session, fam["family"].id, today=MON) == 0

    assert statuses(db_session, done, approved, today) == [TaskStatus.COMPLETED, TaskStatus.APPROVED, TaskStatus.OPEN]


def test_expiring_twice_changes_nothing_more(db_session, fam):
    template = add_template(db_session, fam["family"])
    assignment = add_assignment(db_session, fam["family"], fam["mia"], template)
    add_instance(db_session, template, fam["mia"], d(-1), assignment=assignment)
    add_instance(db_session, template, fam["mia"], MON, assignment=assignment)

    assert expire_stale_instances(db_session, fam["family"].id, today=MON) == 1
    assert expire_stale_instances(db_session, fam["family"].id, today=MON) == 0


# ------------------------------------------------------------------------------------- carry_over


def test_carry_over_templates_keep_every_missed_instance(db_session, fam):
    template = add_template(db_session, fam["family"], carry_over=True)
    assignment = add_assignment(db_session, fam["family"], fam["mia"], template)
    rows = [add_instance(db_session, template, fam["mia"], d(i), assignment=assignment) for i in range(-3, 1)]

    assert expire_stale_instances(db_session, fam["family"].id, today=MON) == 0

    assert statuses(db_session, *rows) == [TaskStatus.OPEN] * 4


def test_carry_over_is_decided_per_template(db_session, fam):
    keep = add_template(db_session, fam["family"], carry_over=True, title="Behalten")
    drop = add_template(db_session, fam["family"], carry_over=False, title="Verfallen")
    keep_assignment = add_assignment(db_session, fam["family"], fam["mia"], keep)
    drop_assignment = add_assignment(db_session, fam["family"], fam["mia"], drop)
    old_keep = add_instance(db_session, keep, fam["mia"], d(-1), assignment=keep_assignment)
    add_instance(db_session, keep, fam["mia"], MON, assignment=keep_assignment)
    old_drop = add_instance(db_session, drop, fam["mia"], d(-1), assignment=drop_assignment)
    add_instance(db_session, drop, fam["mia"], MON, assignment=drop_assignment)

    expire_stale_instances(db_session, fam["family"].id, today=MON)

    assert statuses(db_session, old_keep, old_drop) == [TaskStatus.OPEN, TaskStatus.EXPIRED]


# ------------------------------------------------------------------------------------ phase groups


def test_each_phase_is_its_own_group(db_session, fam):
    family = fam["family"]
    morning = add_phase(db_session, family, name="Morgens", start=(6, 0), end=(8, 0))
    evening = add_phase(db_session, family, name="Abends", start=(18, 0), end=(21, 0))
    template = add_template(db_session, family, phases=[morning, evening])
    assignment = add_assignment(db_session, family, fam["mia"], template)
    mia = fam["mia"]
    m_mon = add_instance(db_session, template, mia, MON, assignment=assignment, phase=morning)
    m_tue = add_instance(db_session, template, mia, d(1), assignment=assignment, phase=morning)
    m_wed = add_instance(db_session, template, mia, WED, assignment=assignment, phase=morning)
    e_mon = add_instance(db_session, template, mia, MON, assignment=assignment, phase=evening)
    e_tue = add_instance(db_session, template, mia, d(1), assignment=assignment, phase=evening)

    # Wednesday: the morning group has a newer instance, the evening group's latest is Tuesday.
    assert expire_stale_instances(db_session, family.id, today=WED) == 3

    assert statuses(db_session, m_mon, m_tue, m_wed) == [TaskStatus.EXPIRED, TaskStatus.EXPIRED, TaskStatus.OPEN]
    assert statuses(db_session, e_mon, e_tue) == [TaskStatus.EXPIRED, TaskStatus.OPEN]


def test_phase_and_plain_instances_of_one_assignment_do_not_mix(db_session, fam):
    phase = add_phase(db_session, fam["family"])
    template = add_template(db_session, fam["family"], phases=[phase])
    assignment = add_assignment(db_session, fam["family"], fam["mia"], template)
    legacy = add_instance(db_session, template, fam["mia"], d(-1), assignment=assignment)  # no phase
    phased = add_instance(db_session, template, fam["mia"], MON, assignment=assignment, phase=phase)

    expire_stale_instances(db_session, fam["family"].id, today=MON)

    # The plain group's latest due instance is its own (yesterday): it is not expired by the phase one.
    assert statuses(db_session, legacy, phased) == [TaskStatus.OPEN, TaskStatus.OPEN]


# --------------------------------------------------------------------------- one-off and tenancy


def test_one_off_instances_never_expire(db_session, fam):
    template = add_template(db_session, fam["family"])
    old = add_instance(db_session, template, fam["mia"], d(-30))
    newer = add_instance(db_session, template, fam["mia"], d(-1))

    assert expire_stale_instances(db_session, fam["family"].id, today=MON) == 0

    assert statuses(db_session, old, newer) == [TaskStatus.OPEN, TaskStatus.OPEN]


def test_other_families_are_not_touched(db_session, fam, other):
    template = add_template(db_session, other["family"])
    assignment = add_assignment(db_session, other["family"], other["mia"], template)
    old = add_instance(db_session, template, other["mia"], d(-1), assignment=assignment)
    add_instance(db_session, template, other["mia"], MON, assignment=assignment)

    assert expire_stale_instances(db_session, fam["family"].id, today=MON) == 0

    assert statuses(db_session, old) == [TaskStatus.OPEN]


# ------------------------------------------------------------------------------------------- API


def setup_daily(db_session, fam):
    template = add_template(db_session, fam["family"])
    assignment = add_assignment(db_session, fam["family"], fam["mia"], template)
    missed = add_instance(db_session, template, fam["mia"], d(-1), assignment=assignment)
    current = add_instance(db_session, template, fam["mia"], MON, assignment=assignment)
    return missed, current


def test_listing_expires_stale_instances_and_returns_them_without_a_filter(client, db_session, fam, monkeypatch):
    freeze_clock(monkeypatch)
    missed, current = setup_daily(db_session, fam)

    rows = client.get(INSTANCES, headers=headers_for(fam["mia"])).json()

    assert {r["id"]: r["status"] for r in rows} == {str(missed.id): "expired", str(current.id): "open"}
    assert statuses(db_session, missed) == [TaskStatus.EXPIRED]  # persisted, not just displayed


def test_status_filter_expired_and_open(client, db_session, fam, monkeypatch):
    freeze_clock(monkeypatch)
    missed, current = setup_daily(db_session, fam)
    headers = headers_for(fam["papa"])

    expired = client.get(INSTANCES, params={"status_filter": "expired"}, headers=headers).json()
    open_ = client.get(INSTANCES, params={"status_filter": "open"}, headers=headers).json()

    assert [r["id"] for r in expired] == [str(missed.id)]
    assert [r["id"] for r in open_] == [str(current.id)]
    assert client.get(INSTANCES, params={"status_filter": "bogus"}, headers=headers).status_code == 422


def test_listing_the_wall_dashboard_expires_too_and_stays_family_scoped(client, db_session, fam, other, monkeypatch):
    freeze_clock(monkeypatch)
    missed, _ = setup_daily(db_session, fam)
    foreign_template = add_template(db_session, other["family"])
    foreign_assignment = add_assignment(db_session, other["family"], other["mia"], foreign_template)
    foreign_old = add_instance(db_session, foreign_template, other["mia"], d(-1), assignment=foreign_assignment)
    add_instance(db_session, foreign_template, other["mia"], MON, assignment=foreign_assignment)

    rows = client.get(INSTANCES, headers=headers_for(fam["wall"])).json()

    assert str(foreign_old.id) not in {r["id"] for r in rows}
    assert statuses(db_session, missed) == [TaskStatus.EXPIRED]
    assert statuses(db_session, foreign_old) == [TaskStatus.OPEN]  # another family's list call decides that


def test_complete_reopen_and_approve_on_an_expired_instance_are_400(client, db_session, fam, monkeypatch):
    freeze_clock(monkeypatch)
    missed, _ = setup_daily(db_session, fam)
    client.get(INSTANCES, headers=headers_for(fam["mia"]))  # expires it
    url = f"{INSTANCES}/{missed.id}"

    for who, action in (("mia", "complete"), ("mia", "reopen"), ("papa", "complete"), ("papa", "reopen"),
                        ("papa", "approve")):
        response = client.post(f"{url}/{action}", headers=headers_for(fam[who]))
        assert response.status_code == 400, (who, action)
        assert response.json()["detail"] == EXPIRED_MSG
    assert statuses(db_session, missed) == [TaskStatus.EXPIRED]


def test_approving_a_foreign_expired_instance_is_still_404(client, db_session, fam, other, monkeypatch):
    freeze_clock(monkeypatch)
    missed, _ = setup_daily(db_session, fam)
    client.get(INSTANCES, headers=headers_for(fam["mia"]))

    response = client.post(f"{INSTANCES}/{missed.id}/approve", headers=headers_for(other["papa"]))

    assert response.status_code == 404


def test_an_expired_instance_gives_no_points(client, db_session, fam, monkeypatch):
    from app.models.points import PointsLedgerEntry

    freeze_clock(monkeypatch)
    missed, _ = setup_daily(db_session, fam)
    client.get(INSTANCES, headers=headers_for(fam["mia"]))

    client.post(f"{INSTANCES}/{missed.id}/approve", headers=headers_for(fam["papa"]))

    assert db_session.scalars(select(PointsLedgerEntry)).all() == []


def test_a_carry_over_instance_stays_completable_after_listing(client, db_session, fam, monkeypatch):
    freeze_clock(monkeypatch)
    template = add_template(db_session, fam["family"], carry_over=True)
    assignment = add_assignment(db_session, fam["family"], fam["mia"], template)
    missed = add_instance(db_session, template, fam["mia"], d(-1), assignment=assignment)
    add_instance(db_session, template, fam["mia"], MON, assignment=assignment)

    rows = client.get(INSTANCES, headers=headers_for(fam["mia"])).json()
    response = client.post(f"{INSTANCES}/{missed.id}/complete", headers=headers_for(fam["mia"]))

    assert {r["status"] for r in rows} == {"open"}
    assert response.status_code == 200 and response.json()["status"] == "completed"


def test_the_worker_job_expires_stale_instances(db_session, fam, monkeypatch):
    from app.worker import sync_worker

    freeze_clock(monkeypatch)
    monkeypatch.setattr(sync_worker, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    missed, _ = setup_daily(db_session, fam)

    sync_worker.generate_recurring_task_instances()

    assert statuses(db_session, missed) == [TaskStatus.EXPIRED]


def test_expiry_uses_the_family_local_day(db_session, fam):
    template = add_template(db_session, fam["family"])
    assignment = add_assignment(db_session, fam["family"], fam["mia"], template)
    yesterday = add_instance(db_session, template, fam["mia"], date(2026, 11, 1), assignment=assignment)
    today = add_instance(db_session, template, fam["mia"], date(2026, 11, 2), assignment=assignment)

    assert expire_stale_instances(db_session, fam["family"].id, today=date(2026, 11, 1)) == 0
    assert expire_stale_instances(db_session, fam["family"].id, today=date(2026, 11, 2)) == 1

    assert statuses(db_session, yesterday, today) == [TaskStatus.EXPIRED, TaskStatus.OPEN]
