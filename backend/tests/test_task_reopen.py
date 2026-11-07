"""Undo of 'ich bin fertig': POST /tasks/instances/{id}/reopen (COMPLETED -> OPEN)."""

import uuid
from datetime import date, timedelta

from sqlalchemy import func, select

from app.core.security import hash_secret
from app.models.points import EarningsLedgerEntry, PointsLedgerEntry
from app.models.task import TaskInstance, TaskStatus
from app.models.user import User, UserRole


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
        "/auth/pin",
        json={"family_id": str(user.family_id), "user_id": str(user.id), "pin": pin},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _setup(client, db_session, email="eltern@example.ch", kind="basis", **template_extra):
    parent_headers = _register_parent(client, email=email)
    admin = db_session.scalar(select(User).where(User.email == email))
    child = _add_user(db_session, admin.family_id, UserRole.CHILD, "Kind")
    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Teller wegräumen", "kind": kind, "icon": "🍽️", **template_extra},
    ).json()
    instance = client.post(
        "/tasks/instances",
        headers=parent_headers,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child.id),
            "due_date": date.today().isoformat(),
        },
    ).json()
    return parent_headers, admin, child, _pin_headers(client, child), instance


def _complete(client, instance, headers):
    return client.post(f"/tasks/instances/{instance['id']}/complete", headers=headers)


def _reopen(client, instance_id, headers=None):
    return client.post(f"/tasks/instances/{instance_id}/reopen", headers=headers)


def test_child_can_reopen_own_completed_task(client, db_session):
    _, _, _, child_headers, instance = _setup(client, db_session)
    assert _complete(client, instance, child_headers).status_code == 200

    response = _reopen(client, instance["id"], child_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "open"
    assert body["completed_at"] is None


def test_reopen_response_exposes_template_fields_for_the_tile(client, db_session):
    _, _, _, child_headers, instance = _setup(
        client, db_session, kind="bonus", points_value=10, bonus_multiplier=2.0
    )
    _complete(client, instance, child_headers)

    body = _reopen(client, instance["id"], child_headers).json()

    template = body["template"]
    assert template["title"] == "Teller wegräumen"
    assert template["icon"] == "🍽️"
    assert template["kind"] == "bonus"
    assert template["points_value"] == 10
    assert template["bonus_multiplier"] == 2.0
    assert "money_value_chf" in template


def test_reopen_clears_completed_at_in_database(client, db_session):
    _, _, _, child_headers, instance = _setup(client, db_session)
    _complete(client, instance, child_headers)
    stored = db_session.get(TaskInstance, uuid.UUID(instance["id"]))
    db_session.refresh(stored)
    assert stored.completed_at is not None

    _reopen(client, instance["id"], child_headers)

    db_session.refresh(stored)
    assert stored.status == TaskStatus.OPEN
    assert stored.completed_at is None


def test_complete_reopen_complete_again_works(client, db_session):
    _, _, _, child_headers, instance = _setup(client, db_session)
    assert _complete(client, instance, child_headers).status_code == 200
    assert _reopen(client, instance["id"], child_headers).status_code == 200

    again = _complete(client, instance, child_headers)

    assert again.status_code == 200
    assert again.json()["status"] == "completed"
    assert again.json()["completed_at"] is not None


def test_sibling_cannot_reopen(client, db_session):
    _, admin, _, child_headers, instance = _setup(client, db_session)
    sibling = _add_user(db_session, admin.family_id, UserRole.CHILD, "Geschwister", pin="9999")
    _complete(client, instance, child_headers)

    response = _reopen(client, instance["id"], _pin_headers(client, sibling, pin="9999"))

    assert response.status_code == 404
    assert response.json()["detail"] == "Aufgabe nicht gefunden"
    stored = db_session.get(TaskInstance, uuid.UUID(instance["id"]))
    db_session.refresh(stored)
    assert stored.status == TaskStatus.COMPLETED


def test_parent_can_reopen_childs_completed_task(client, db_session):
    parent_headers, _, _, child_headers, instance = _setup(client, db_session)
    _complete(client, instance, child_headers)

    response = _reopen(client, instance["id"], parent_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "open"


def test_parent_member_can_reopen(client, db_session):
    _, admin, _, child_headers, instance = _setup(client, db_session)
    member = User(
        family_id=admin.family_id,
        role=UserRole.PARENT_MEMBER,
        display_name="Mama",
        email="mama@example.ch",
        password_hash=hash_secret("s3hr-sicher"),
    )
    db_session.add(member)
    db_session.commit()
    login = client.post("/auth/login", json={"email": "mama@example.ch", "password": "s3hr-sicher"})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    _complete(client, instance, child_headers)

    response = _reopen(client, instance["id"], member_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "open"


def test_dashboard_user_can_reopen_on_behalf(client, db_session):
    _, admin, _, child_headers, instance = _setup(client, db_session)
    wall = _add_user(db_session, admin.family_id, UserRole.DASHBOARD, "Wand-Dashboard", pin="2468")
    _complete(client, instance, child_headers)

    response = _reopen(client, instance["id"], _pin_headers(client, wall, pin="2468"))

    assert response.status_code == 200
    assert response.json()["status"] == "open"


def test_other_family_cannot_reopen(client, db_session):
    _, _, _, child_headers, instance = _setup(client, db_session)
    _complete(client, instance, child_headers)
    other_parent = _register_parent(client, email="fremd@example.ch", family_name="Fremd")

    response = _reopen(client, instance["id"], other_parent)

    assert response.status_code == 404
    assert response.json()["detail"] == "Aufgabe nicht gefunden"
    stored = db_session.get(TaskInstance, uuid.UUID(instance["id"]))
    db_session.refresh(stored)
    assert stored.status == TaskStatus.COMPLETED


def test_other_family_dashboard_cannot_reopen(client, db_session):
    _, _, _, child_headers, instance = _setup(client, db_session)
    _complete(client, instance, child_headers)
    _register_parent(client, email="fremd@example.ch", family_name="Fremd")
    other_admin = db_session.scalar(select(User).where(User.email == "fremd@example.ch"))
    other_wall = _add_user(db_session, other_admin.family_id, UserRole.DASHBOARD, "Wand", pin="2468")

    response = _reopen(client, instance["id"], _pin_headers(client, other_wall, pin="2468"))

    assert response.status_code == 404


def test_approved_task_cannot_be_reopened_and_ledgers_stay(client, db_session):
    parent_headers, _, child, child_headers, instance = _setup(
        client, db_session, kind="geld", money_value_chf=5.0
    )
    _complete(client, instance, child_headers)
    assert client.post(f"/tasks/instances/{instance['id']}/approve", headers=parent_headers).status_code == 200
    ledgers_before = (
        db_session.scalar(select(func.count()).select_from(PointsLedgerEntry)),
        db_session.scalar(select(func.count()).select_from(EarningsLedgerEntry)),
    )

    for headers in (child_headers, parent_headers):
        response = _reopen(client, instance["id"], headers)
        assert response.status_code == 400
        assert response.json()["detail"] == "Aufgabe wurde bereits von den Eltern bestätigt."

    stored = db_session.get(TaskInstance, uuid.UUID(instance["id"]))
    db_session.refresh(stored)
    assert stored.status == TaskStatus.APPROVED
    assert ledgers_before == (
        db_session.scalar(select(func.count()).select_from(PointsLedgerEntry)),
        db_session.scalar(select(func.count()).select_from(EarningsLedgerEntry)),
    )


def test_open_task_cannot_be_reopened(client, db_session):
    _, _, _, child_headers, instance = _setup(client, db_session)

    response = _reopen(client, instance["id"], child_headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "Aufgabe ist nicht als erledigt markiert."


def test_late_completed_past_task_can_be_reopened(client, db_session):
    """No date rule: a past-due task that was completed late may be reopened as long as it is not approved."""
    _, _, _, child_headers, instance = _setup(client, db_session)
    stored = db_session.get(TaskInstance, uuid.UUID(instance["id"]))
    stored.due_date = date.today() - timedelta(days=2)
    db_session.commit()
    _complete(client, instance, child_headers)

    response = _reopen(client, instance["id"], child_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "open"


def test_unknown_instance_returns_404(client, db_session):
    parent_headers, _, _, _, _ = _setup(client, db_session)

    response = _reopen(client, str(uuid.uuid4()), parent_headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Aufgabe nicht gefunden"


def test_malformed_id_is_rejected_with_422_like_complete(client, db_session):
    parent_headers, _, _, _, _ = _setup(client, db_session)

    reopen = _reopen(client, "not-a-uuid", parent_headers)
    complete = client.post("/tasks/instances/not-a-uuid/complete", headers=parent_headers)

    assert reopen.status_code == 422
    assert complete.status_code == 422


def test_reopen_requires_authentication(client, db_session):
    _, _, _, _, instance = _setup(client, db_session)

    response = client.post(f"/tasks/instances/{instance['id']}/reopen")

    assert response.status_code == 401
