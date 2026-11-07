"""Edit (PATCH) and delete/archive (DELETE) of task templates."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from app.core.security import hash_secret
from app.core.task_recurrence import generate_instances_for_assignment
from app.models.points import EarningsLedgerEntry, PointsLedgerEntry
from app.models.task import TaskAssignment, TaskInstance, TaskStatus, TaskTemplate
from app.models.user import User, UserRole

LOCKED_DETAIL = "Art und Punkte lassen sich nicht mehr ändern, weil schon Aufgaben bestätigt wurden."
NOT_FOUND = "Vorlage nicht gefunden"


def _register_parent(client, email="eltern@example.ch", family_name="Muster"):
    response = client.post(
        "/auth/register-family",
        json={
            "family_name": family_name,
            "admin_display_name": "Papa",
            "admin_email": email,
            "admin_password": "s3hr-sicher",
        },
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _add_user(db_session, family_id, role, name, pin="1234"):
    user = User(family_id=family_id, role=role, display_name=name, pin_hash=hash_secret(pin))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _pin_headers(client, user, pin="1234"):
    login = client.post(
        "/auth/pin", json={"family_id": str(user.family_id), "user_id": str(user.id), "pin": pin}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _family(client, db_session, email="eltern@example.ch", family_name="Muster"):
    headers = _register_parent(client, email=email, family_name=family_name)
    admin = db_session.scalar(select(User).where(User.email == email))
    child = _add_user(db_session, admin.family_id, UserRole.CHILD, "Kind")
    return headers, admin, child


def _template(client, headers, **extra):
    body = {"title": "Essensbox ausräumen", "kind": "basis", **extra}
    response = client.post("/tasks/templates", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _instance(client, headers, template, child, **extra):
    response = client.post(
        "/tasks/instances",
        headers=headers,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child.id),
            "due_date": date.today().isoformat(),
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve(client, db_session, headers, child_headers, instance):
    assert client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers).status_code == 200
    response = client.post(f"/tasks/instances/{instance['id']}/approve", headers=headers)
    assert response.status_code == 200, response.text


def _patch(client, template_id, headers=None, **body):
    return client.patch(f"/tasks/templates/{template_id}", headers=headers, json=body)


def _delete(client, template_id, headers=None):
    return client.delete(f"/tasks/templates/{template_id}", headers=headers)


# ---------------------------------------------------------------- PATCH: basics


def test_patch_title_and_icon_returns_updated_template(client, db_session):
    headers, _, _ = _family(client, db_session)
    template = _template(client, headers)

    response = _patch(client, template["id"], headers, title="Brotdose ausräumen", icon="🍱")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == template["id"]
    assert body["title"] == "Brotdose ausräumen"
    assert body["icon"] == "🍱"
    assert body["kind"] == "basis"
    listed = client.get("/tasks/templates", headers=headers).json()
    assert [t["title"] for t in listed] == ["Brotdose ausräumen"]


def test_patch_icon_null_clears_icon_but_omitted_icon_keeps_it(client, db_session):
    headers, _, _ = _family(client, db_session)
    template = _template(client, headers, icon="🍱")

    kept = _patch(client, template["id"], headers, title="Neu")
    assert kept.json()["icon"] == "🍱"

    cleared = _patch(client, template["id"], headers, icon=None)
    assert cleared.status_code == 200
    assert cleared.json()["icon"] is None
    assert cleared.json()["title"] == "Neu"


def test_patch_empty_body_is_a_noop(client, db_session):
    headers, _, _ = _family(client, db_session)
    template = _template(client, headers, kind="bonus", points_value=10)

    response = _patch(client, template["id"], headers)

    assert response.status_code == 200
    assert response.json() == template


def test_patch_rejects_blank_or_too_long_title_and_long_icon(client, db_session):
    headers, _, _ = _family(client, db_session)
    template = _template(client, headers)

    assert _patch(client, template["id"], headers, title="").status_code == 422
    assert _patch(client, template["id"], headers, title="   ").status_code == 422
    assert _patch(client, template["id"], headers, title="x" * 121).status_code == 422
    assert _patch(client, template["id"], headers, title="x" * 120).status_code == 200
    assert _patch(client, template["id"], headers, icon="123456789").status_code == 422
    assert _patch(client, template["id"], headers, title=None).status_code == 422


def test_patch_changes_kind_points_when_nothing_approved_yet(client, db_session):
    headers, _, child = _family(client, db_session)
    template = _template(client, headers)
    _instance(client, headers, template, child)  # open instance only

    response = _patch(client, template["id"], headers, kind="geld", money_value_chf=2.5)

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "geld"
    assert body["money_value_chf"] == 2.5


# ---------------------------------------------------------------- PATCH: validation (same rules as create)


def test_patch_validates_merged_values_like_create(client, db_session):
    headers, _, _ = _family(client, db_session)
    basis = _template(client, headers)
    bonus = _template(client, headers, title="Bonus", kind="bonus", points_value=10, bonus_multiplier=2.0)

    # basis tasks must have 0 points
    assert _patch(client, basis["id"], headers, points_value=5).status_code == 422
    # bonus multiplier only for bonus tasks
    assert _patch(client, basis["id"], headers, bonus_multiplier=2.0).status_code == 422
    # multiplier range (0, 5]
    assert _patch(client, bonus["id"], headers, bonus_multiplier=0).status_code == 422
    assert _patch(client, bonus["id"], headers, bonus_multiplier=5.5).status_code == 422
    assert _patch(client, bonus["id"], headers, bonus_multiplier=5).status_code == 200
    # switching an existing bonus (multiplier 5, points 10) to basis violates basis rules
    assert _patch(client, bonus["id"], headers, kind="basis").status_code == 422
    # ...but is fine together with the fixing fields
    ok = _patch(client, bonus["id"], headers, kind="basis", points_value=0, bonus_multiplier=1.0)
    assert ok.status_code == 200
    # negative values and unknown kind
    assert _patch(client, bonus["id"], headers, kind="geld", points_value=-1).status_code == 422
    assert _patch(client, bonus["id"], headers, money_value_chf=-0.5).status_code == 422
    assert _patch(client, bonus["id"], headers, kind="gibtsnicht").status_code == 422


def test_patch_422_leaves_template_unchanged(client, db_session):
    headers, _, _ = _family(client, db_session)
    template = _template(client, headers)

    assert _patch(client, template["id"], headers, title="Neu", points_value=5).status_code == 422

    assert client.get("/tasks/templates", headers=headers).json() == [template]


# ---------------------------------------------------------------- PATCH: approved lock


def _approved_setup(client, db_session, **template_extra):
    headers, admin, child = _family(client, db_session)
    template = _template(client, headers, kind="bonus", points_value=10, **template_extra)
    instance = _instance(client, headers, template, child)
    _approve(client, db_session, headers, _pin_headers(client, child), instance)
    return headers, admin, child, template


def test_patch_economics_after_approval_is_409(client, db_session):
    headers, _, _, template = _approved_setup(client, db_session)

    for change in (
        {"points_value": 20},
        {"kind": "geld", "money_value_chf": 1.0, "points_value": 0},
        {"bonus_multiplier": 2.0},
        {"money_value_chf": 3.0},
    ):
        response = _patch(client, template["id"], headers, **change)
        assert response.status_code == 409, change
        assert response.json()["detail"] == LOCKED_DETAIL

    assert client.get("/tasks/templates", headers=headers).json() == [template]


def test_patch_after_approval_allows_title_icon_and_unchanged_economics(client, db_session):
    headers, _, _, template = _approved_setup(client, db_session)

    response = _patch(
        client,
        template["id"],
        headers,
        title="Neuer Titel",
        icon="⭐",
        kind="bonus",  # resent but unchanged
        points_value=10,
        bonus_multiplier=1.0,
        money_value_chf=0.0,
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Neuer Titel"
    assert response.json()["icon"] == "⭐"
    assert response.json()["points_value"] == 10


def test_patch_unapproved_instances_do_not_lock(client, db_session):
    headers, _, child = _family(client, db_session)
    template = _template(client, headers, kind="bonus", points_value=10)
    first = _instance(client, headers, template, child)
    second = _instance(client, headers, template, child)
    client.post(f"/tasks/instances/{first['id']}/complete", headers=_pin_headers(client, child))
    assert second["status"] == "open"

    assert _patch(client, template["id"], headers, points_value=15).status_code == 200  # open + completed only


def test_approved_instance_of_other_template_does_not_lock(client, db_session):
    headers, _, child = _family(client, db_session)
    approved_tpl = _template(client, headers, title="A", kind="bonus", points_value=10)
    other_tpl = _template(client, headers, title="B", kind="bonus", points_value=10)
    _approve(client, db_session, headers, _pin_headers(client, child), _instance(client, headers, approved_tpl, child))

    assert _patch(client, other_tpl["id"], headers, points_value=99).status_code == 200


# ---------------------------------------------------------------- PATCH: 404 / 422 / auth / tenancy


def test_patch_unknown_malformed_and_foreign_template(client, db_session):
    headers_a, _, _ = _family(client, db_session, email="a@example.ch", family_name="A")
    headers_b, _, _ = _family(client, db_session, email="b@example.ch", family_name="B")
    template_a = _template(client, headers_a)

    unknown = _patch(client, uuid.uuid4(), headers_a, title="x")
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == NOT_FOUND
    assert _patch(client, "not-a-uuid", headers_a, title="x").status_code == 422

    foreign = _patch(client, template_a["id"], headers_b, title="Gehackt")
    assert foreign.status_code == 404
    assert foreign.json()["detail"] == NOT_FOUND
    assert client.get("/tasks/templates", headers=headers_a).json()[0]["title"] == "Essensbox ausräumen"


def test_patch_and_delete_require_parent(client, db_session):
    headers, admin, child = _family(client, db_session)
    dashboard = _add_user(db_session, admin.family_id, UserRole.DASHBOARD, "Wand", pin="9999")
    template = _template(client, headers)
    child_headers = _pin_headers(client, child)
    dash_headers = _pin_headers(client, dashboard, pin="9999")

    for denied in (child_headers, dash_headers):
        assert _patch(client, template["id"], denied, title="x").status_code == 403
        assert _delete(client, template["id"], denied).status_code == 403
    assert _patch(client, template["id"], None, title="x").status_code == 401
    assert _delete(client, template["id"], None).status_code == 401
    assert client.get("/tasks/templates", headers=headers).json() == [template]


# ---------------------------------------------------------------- DELETE


def test_delete_unreferenced_template_is_hard_deleted(client, db_session):
    headers, _, _ = _family(client, db_session)
    template = _template(client, headers)

    response = _delete(client, template["id"], headers)

    assert response.status_code == 200
    assert response.json() == {"result": "deleted"}
    assert client.get("/tasks/templates", headers=headers).json() == []
    assert db_session.get(TaskTemplate, uuid.UUID(template["id"])) is None
    assert _delete(client, template["id"], headers).status_code == 404


def test_delete_template_with_instance_is_archived(client, db_session):
    headers, _, child = _family(client, db_session)
    template = _template(client, headers, icon="🍱")
    instance = _instance(client, headers, template, child)

    response = _delete(client, template["id"], headers)

    assert response.status_code == 200
    assert response.json() == {"result": "archived"}
    assert client.get("/tasks/templates", headers=headers).json() == []
    db_session.expire_all()
    stored = db_session.get(TaskTemplate, uuid.UUID(template["id"]))
    assert stored is not None
    assert stored.archived_at is not None and stored.archived_at.tzinfo is not None
    # the open instance stays visible with its template info and can still be completed
    listed = client.get("/tasks/instances", headers=headers).json()
    assert [i["id"] for i in listed] == [instance["id"]]
    assert listed[0]["template"]["icon"] == "🍱"
    assert listed[0]["template"]["title"] == "Essensbox ausräumen"
    done = client.post(f"/tasks/instances/{instance['id']}/complete", headers=_pin_headers(client, child))
    assert done.status_code == 200
    assert done.json()["template"]["icon"] == "🍱"


def test_delete_template_with_only_an_assignment_is_archived(client, db_session):
    headers, admin, child = _family(client, db_session)
    template = _template(client, headers)
    db_session.add(
        TaskAssignment(
            family_id=admin.family_id,
            template_id=uuid.UUID(template["id"]),
            assignee_user_id=child.id,
            recurrence_rule="daily",
            start_date=date.today(),
        )
    )
    db_session.commit()

    assert _delete(client, template["id"], headers).json() == {"result": "archived"}
    assert db_session.get(TaskTemplate, uuid.UUID(template["id"])) is not None


def test_delete_archived_template_again_is_404_and_patch_is_404(client, db_session):
    headers, _, child = _family(client, db_session)
    template = _template(client, headers)
    _instance(client, headers, template, child)
    assert _delete(client, template["id"], headers).json() == {"result": "archived"}

    again = _delete(client, template["id"], headers)
    assert again.status_code == 404
    assert again.json()["detail"] == NOT_FOUND
    patched = _patch(client, template["id"], headers, title="x")
    assert patched.status_code == 404
    assert patched.json()["detail"] == NOT_FOUND


def test_delete_unknown_malformed_and_foreign_template(client, db_session):
    headers_a, _, _ = _family(client, db_session, email="a@example.ch", family_name="A")
    headers_b, _, _ = _family(client, db_session, email="b@example.ch", family_name="B")
    template_a = _template(client, headers_a)

    assert _delete(client, uuid.uuid4(), headers_a).status_code == 404
    assert _delete(client, "not-a-uuid", headers_a).status_code == 422
    foreign = _delete(client, template_a["id"], headers_b)
    assert foreign.status_code == 404
    assert foreign.json()["detail"] == NOT_FOUND
    assert len(client.get("/tasks/templates", headers=headers_a).json()) == 1


def test_family_b_does_not_see_family_a_templates(client, db_session):
    headers_a, _, _ = _family(client, db_session, email="a@example.ch", family_name="A")
    headers_b, _, _ = _family(client, db_session, email="b@example.ch", family_name="B")
    _template(client, headers_a)

    assert client.get("/tasks/templates", headers=headers_b).json() == []


def test_archiving_keeps_approved_history_and_ledgers_untouched(client, db_session):
    headers, _, child, template = _approved_setup(client, db_session)
    ledger_before = (
        db_session.scalar(select(func.count()).select_from(PointsLedgerEntry)),
        db_session.scalar(select(func.sum(PointsLedgerEntry.delta))),
        db_session.scalar(select(func.count()).select_from(EarningsLedgerEntry)),
    )
    instances_before = db_session.scalar(select(func.count()).select_from(TaskInstance))

    assert _delete(client, template["id"], headers).json() == {"result": "archived"}

    db_session.expire_all()
    assert (
        db_session.scalar(select(func.count()).select_from(PointsLedgerEntry)),
        db_session.scalar(select(func.sum(PointsLedgerEntry.delta))),
        db_session.scalar(select(func.count()).select_from(EarningsLedgerEntry)),
    ) == ledger_before
    assert db_session.scalar(select(func.count()).select_from(TaskInstance)) == instances_before
    approved = db_session.scalars(select(TaskInstance).where(TaskInstance.status == TaskStatus.APPROVED)).all()
    assert len(approved) == 1
    history = client.get("/tasks/instances?status_filter=approved", headers=headers).json()
    assert history[0]["template"]["title"] == "Essensbox ausräumen"


# ---------------------------------------------------------------- archived templates elsewhere


def test_create_instance_with_archived_template_is_404(client, db_session):
    headers, _, child = _family(client, db_session)
    template = _template(client, headers)
    _instance(client, headers, template, child)
    _delete(client, template["id"], headers)

    response = client.post(
        "/tasks/instances",
        headers=headers,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child.id),
            "due_date": date.today().isoformat(),
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Familienmitglied oder Aufgaben-Vorlage nicht gefunden"
    recurring = client.post(
        "/tasks/instances",
        headers=headers,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child.id),
            "due_date": date.today().isoformat(),
            "recurrence_rule": "daily",
        },
    )
    assert recurring.status_code == 404


def test_recurrence_generation_skips_archived_templates(client, db_session):
    headers, _, child = _family(client, db_session)
    template = _template(client, headers)
    _instance(client, headers, template, child, recurrence_rule="daily")
    assignment = db_session.scalar(select(TaskAssignment))
    existing = db_session.scalar(select(func.count()).select_from(TaskInstance))
    assert existing > 1

    assert _delete(client, template["id"], headers).json() == {"result": "archived"}
    db_session.expire_all()
    far_future = date.today() + timedelta(days=30)

    created = generate_instances_for_assignment(db_session, assignment, today=far_future)

    assert created == []
    assert db_session.scalar(select(func.count()).select_from(TaskInstance)) == existing


def test_recurrence_generation_still_works_for_active_templates(client, db_session):
    headers, _, child = _family(client, db_session)
    template = _template(client, headers)
    _instance(client, headers, template, child, recurrence_rule="daily")
    assignment = db_session.scalar(select(TaskAssignment))

    created = generate_instances_for_assignment(db_session, assignment, today=date.today() + timedelta(days=30))

    assert created


def test_worker_job_skips_archived_templates(client, db_session, monkeypatch):
    from app.worker import sync_worker

    headers, _, child = _family(client, db_session)
    active = _template(client, headers, title="Aktiv")
    archived = _template(client, headers, title="Archiviert")
    _instance(client, headers, active, child, recurrence_rule="daily")
    _instance(client, headers, archived, child, recurrence_rule="daily")
    _delete(client, archived["id"], headers)

    seen: list[uuid.UUID] = []
    monkeypatch.setattr(sync_worker, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        sync_worker, "generate_instances_for_assignment", lambda db, assignment: seen.append(assignment.template_id)
    )

    sync_worker.generate_recurring_task_instances()

    assert seen == [uuid.UUID(active["id"])]


def test_archived_at_is_timezone_aware_now(client, db_session):
    headers, _, child = _family(client, db_session)
    template = _template(client, headers)
    _instance(client, headers, template, child)
    before = datetime.now(UTC)

    _delete(client, template["id"], headers)

    db_session.expire_all()
    stored = db_session.get(TaskTemplate, uuid.UUID(template["id"]))
    assert before - timedelta(seconds=5) <= stored.archived_at <= datetime.now(UTC) + timedelta(seconds=5)
