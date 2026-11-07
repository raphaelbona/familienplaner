"""Tests for daily/weekly recurring task assignments."""

from datetime import date, timedelta

from sqlalchemy import select

from app.core.security import hash_secret
from app.core.task_recurrence import (
    DAILY_WINDOW_DAYS,
    WEEKLY_WINDOW_DAYS,
    generate_instances_for_assignment,
)
from app.models.family import Family
from app.models.task import TaskAssignment, TaskInstance, TaskKind, TaskTemplate
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


def _create_child(db_session, family_id, pin="1234"):
    child = User(
        family_id=family_id,
        role=UserRole.CHILD,
        display_name="Kind",
        pin_hash=hash_secret(pin),
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    return child


def _create_dashboard_user(db_session, family_id, pin="9999"):
    dashboard_user = User(
        family_id=family_id,
        role=UserRole.DASHBOARD,
        display_name="Wand-Dashboard",
        pin_hash=hash_secret(pin),
    )
    db_session.add(dashboard_user)
    db_session.commit()
    db_session.refresh(dashboard_user)
    return dashboard_user


def test_template_icon_is_optional_and_round_trips(client):
    """A task template's icon, when given, comes back unchanged; omitting it defaults to None."""
    parent_headers = _register_parent(client, email="templateicon@example.ch")

    with_icon = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Zimmer aufräumen", "kind": "basis", "points_value": 0, "icon": "🧹"},
    )
    assert with_icon.status_code == 201
    assert with_icon.json()["icon"] == "🧹"

    without_icon = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Blumen gießen", "kind": "basis", "points_value": 0},
    )
    assert without_icon.status_code == 201
    assert without_icon.json()["icon"] is None


def test_daily_recurrence_creates_rolling_window(client, db_session):
    """Assigning a task with recurrence_rule=daily generates the whole rolling window."""
    parent_headers = _register_parent(client, email="daily@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "daily@example.ch"))
    child = _create_child(db_session, admin.family_id)

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Zähne putzen", "kind": "basis", "points_value": 0},
    ).json()

    today = date.today()
    response = client.post(
        "/tasks/instances",
        headers=parent_headers,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child.id),
            "due_date": today.isoformat(),
            "recurrence_rule": "daily",
        },
    )

    assert response.status_code == 201
    first_instance = response.json()
    assert first_instance["due_date"] == today.isoformat()

    assignment = db_session.scalar(select(TaskAssignment).where(TaskAssignment.assignee_user_id == child.id))
    assert assignment is not None
    assert assignment.recurrence_rule == "daily"
    assert assignment.start_date == today

    instances = db_session.scalars(
        select(TaskInstance).where(TaskInstance.assignment_id == assignment.id)
    ).all()
    assert len(instances) == DAILY_WINDOW_DAYS + 1
    due_dates = {i.due_date for i in instances}
    assert due_dates == {today + timedelta(days=n) for n in range(DAILY_WINDOW_DAYS + 1)}


def test_weekly_recurrence_creates_rolling_window(client, db_session):
    """Assigning a task with recurrence_rule=weekly generates weekly-spaced occurrences."""
    parent_headers = _register_parent(client, email="weekly@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "weekly@example.ch"))
    child = _create_child(db_session, admin.family_id)

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Rasen mähen", "kind": "bonus", "points_value": 5},
    ).json()

    today = date.today()
    response = client.post(
        "/tasks/instances",
        headers=parent_headers,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child.id),
            "due_date": today.isoformat(),
            "recurrence_rule": "weekly",
        },
    )

    assert response.status_code == 201

    assignment = db_session.scalar(select(TaskAssignment).where(TaskAssignment.assignee_user_id == child.id))
    instances = db_session.scalars(
        select(TaskInstance).where(TaskInstance.assignment_id == assignment.id)
    ).all()

    expected_count = WEEKLY_WINDOW_DAYS // 7 + 1
    assert len(instances) == expected_count
    due_dates = sorted(i.due_date for i in instances)
    assert due_dates[0] == today
    for a, b in zip(due_dates, due_dates[1:], strict=False):
        assert (b - a).days == 7


def test_task_without_recurrence_rule_creates_single_instance(client, db_session):
    """Omitting recurrence_rule keeps the original one-off behavior (no TaskAssignment row)."""
    parent_headers = _register_parent(client, email="once@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "once@example.ch"))
    child = _create_child(db_session, admin.family_id)

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Geschirrspüler ausräumen", "kind": "basis", "points_value": 0},
    ).json()

    response = client.post(
        "/tasks/instances",
        headers=parent_headers,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child.id),
            "due_date": date.today().isoformat(),
        },
    )

    assert response.status_code == 201
    assert response.json()["due_date"] == date.today().isoformat()
    assert db_session.scalars(select(TaskAssignment)).all() == []
    assert len(db_session.scalars(select(TaskInstance)).all()) == 1


def test_recurring_instances_appear_in_instance_list(client, db_session):
    """The generated occurrences show up via GET /tasks/instances like any other instance."""
    parent_headers = _register_parent(client, email="list@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "list@example.ch"))
    child = _create_child(db_session, admin.family_id)

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Tisch decken", "kind": "basis", "points_value": 0},
    ).json()

    client.post(
        "/tasks/instances",
        headers=parent_headers,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child.id),
            "due_date": date.today().isoformat(),
            "recurrence_rule": "daily",
        },
    )

    response = client.get(f"/tasks/instances?assignee_user_id={child.id}", headers=parent_headers)
    assert response.status_code == 200
    assert len(response.json()) == DAILY_WINDOW_DAYS + 1


def test_generate_instances_for_assignment_tops_up_window(db_session):
    """Calling the generator again only adds newly-due dates, never duplicates existing ones."""
    family = Family(name="Topup-Familie")
    db_session.add(family)
    db_session.commit()
    db_session.refresh(family)

    child = _create_child(db_session, family.id)

    template = TaskTemplate(family_id=family.id, title="Müll rausbringen", kind=TaskKind.BASIS, points_value=0)
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    start = date.today() - timedelta(days=5)
    assignment = TaskAssignment(
        family_id=family.id,
        template_id=template.id,
        assignee_user_id=child.id,
        recurrence_rule="daily",
        start_date=start,
    )
    db_session.add(assignment)
    db_session.commit()
    db_session.refresh(assignment)

    first_batch = generate_instances_for_assignment(db_session, assignment)
    assert len(first_batch) == DAILY_WINDOW_DAYS + 1  # clamped to today, not the past start_date

    second_batch = generate_instances_for_assignment(db_session, assignment)
    assert len(second_batch) == 0

    all_instances = db_session.scalars(
        select(TaskInstance).where(TaskInstance.assignment_id == assignment.id)
    ).all()
    assert len(all_instances) == DAILY_WINDOW_DAYS + 1


def test_instance_list_filters_by_due_date_range(client, db_session):
    """due_after/due_before narrow the instance list to a date window (for weekly widgets)."""
    parent_headers = _register_parent(client, email="range@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "range@example.ch"))
    child = _create_child(db_session, admin.family_id)

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Bett machen", "kind": "basis", "points_value": 0},
    ).json()

    today = date.today()
    client.post(
        "/tasks/instances",
        headers=parent_headers,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child.id),
            "due_date": today.isoformat(),
            "recurrence_rule": "daily",
        },
    )

    window_start = today
    window_end = today + timedelta(days=3)
    response = client.get(
        f"/tasks/instances?due_after={window_start.isoformat()}&due_before={window_end.isoformat()}",
        headers=parent_headers,
    )
    assert response.status_code == 200
    due_dates = {i["due_date"] for i in response.json()}
    assert due_dates == {(today + timedelta(days=n)).isoformat() for n in range(4)}


def test_weekly_with_weekdays_recurs_only_on_chosen_days(client, db_session):
    """weekdays=[0, 3] ('zweimal die Woche') due only on Mon and Thu, not once a week."""
    parent_headers = _register_parent(client, email="weekdays@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "weekdays@example.ch"))
    child = _create_child(db_session, admin.family_id)

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Klavier üben", "kind": "basis", "points_value": 0},
    ).json()

    today = date.today()
    response = client.post(
        "/tasks/instances",
        headers=parent_headers,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child.id),
            "due_date": today.isoformat(),
            "recurrence_rule": "weekly",
            "weekdays": [0, 3],
        },
    )
    assert response.status_code == 201

    assignment = db_session.scalar(select(TaskAssignment).where(TaskAssignment.assignee_user_id == child.id))
    assert assignment.weekdays == "0,3"

    instances = db_session.scalars(
        select(TaskInstance).where(TaskInstance.assignment_id == assignment.id)
    ).all()
    assert len(instances) > 0
    for instance in instances:
        assert instance.due_date.weekday() in {0, 3}

    expected_count = sum(
        1
        for n in range(WEEKLY_WINDOW_DAYS + 1)
        if (today + timedelta(days=n)).weekday() in {0, 3}
    )
    assert len(instances) == expected_count


def test_weekdays_rejected_without_weekly_recurrence(client, db_session):
    """weekdays only makes sense for recurrence_rule='weekly'."""
    parent_headers = _register_parent(client, email="badweekdays@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "badweekdays@example.ch"))
    child = _create_child(db_session, admin.family_id)

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Staubsaugen", "kind": "basis", "points_value": 0},
    ).json()

    response = client.post(
        "/tasks/instances",
        headers=parent_headers,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child.id),
            "due_date": date.today().isoformat(),
            "recurrence_rule": "daily",
            "weekdays": [0],
        },
    )
    assert response.status_code == 422


def test_assignee_user_ids_creates_one_instance_per_child(client, db_session):
    """Assigning to multiple children at once creates one task instance per child."""
    parent_headers = _register_parent(client, email="multichild@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "multichild@example.ch"))
    child_a = _create_child(db_session, admin.family_id)
    child_b = User(
        family_id=admin.family_id,
        role=UserRole.CHILD,
        display_name="Kind2",
        pin_hash=hash_secret("5678"),
    )
    db_session.add(child_b)
    db_session.commit()
    db_session.refresh(child_b)

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Tisch abräumen", "kind": "basis", "points_value": 0},
    ).json()

    response = client.post(
        "/tasks/instances",
        headers=parent_headers,
        json={
            "template_id": template["id"],
            "assignee_user_ids": [str(child_a.id), str(child_b.id)],
            "due_date": date.today().isoformat(),
        },
    )
    assert response.status_code == 201

    instances = db_session.scalars(
        select(TaskInstance).where(TaskInstance.template_id == template["id"])
    ).all()
    assert len(instances) == 2
    assignees = {i.assignee_user_id for i in instances}
    assert assignees == {child_a.id, child_b.id}


def test_parent_can_complete_childs_task_on_shared_tablet(client, db_session):
    """A parent (same family) can mark a child's task done without the child logging in."""
    parent_headers = _register_parent(client, email="tablet@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "tablet@example.ch"))
    child = _create_child(db_session, admin.family_id)

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Zähne putzen", "kind": "basis", "points_value": 0},
    ).json()

    instance = client.post(
        "/tasks/instances",
        headers=parent_headers,
        json={"template_id": template["id"], "assignee_user_id": str(child.id), "due_date": date.today().isoformat()},
    ).json()

    response = client.post(f"/tasks/instances/{instance['id']}/complete", headers=parent_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_dashboard_user_can_complete_childs_task_on_wall_tablet(client, db_session):
    parent_headers = _register_parent(client, email="wall-tablet@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "wall-tablet@example.ch"))
    child = _create_child(db_session, admin.family_id)
    dashboard_user = _create_dashboard_user(db_session, admin.family_id, pin="9999")

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Teller weg räumen", "kind": "basis", "points_value": 0},
    ).json()

    instance = client.post(
        "/tasks/instances",
        headers=parent_headers,
        json={"template_id": template["id"], "assignee_user_id": str(child.id), "due_date": date.today().isoformat()},
    ).json()

    login = client.post(
        "/auth/pin",
        json={"family_id": str(admin.family_id), "user_id": str(dashboard_user.id), "pin": "9999"},
    )
    dashboard_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.post(f"/tasks/instances/{instance['id']}/complete", headers=dashboard_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_future_task_cannot_be_completed_early(client, db_session):
    parent_headers = _register_parent(client, email="future-task@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "future-task@example.ch"))
    child = _create_child(db_session, admin.family_id)

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Teller weg räumen", "kind": "basis", "points_value": 0},
    ).json()

    instance = client.post(
        "/tasks/instances",
        headers=parent_headers,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child.id),
            "due_date": (date.today() + timedelta(days=1)).isoformat(),
        },
    ).json()

    response = client.post(f"/tasks/instances/{instance['id']}/complete", headers=parent_headers)
    assert response.status_code == 400
    assert "erst am Fälligkeitstag" in response.json()["detail"]


def test_parent_cannot_complete_another_familys_childs_task(client, db_session):
    """A parent from a different family cannot mark someone else's child's task done."""
    parent_a_headers = _register_parent(client, email="tabletA@example.ch")
    admin_a = db_session.scalar(select(User).where(User.email == "tabletA@example.ch"))
    child_a = _create_child(db_session, admin_a.family_id)

    template = client.post(
        "/tasks/templates",
        headers=parent_a_headers,
        json={"title": "Zähne putzen", "kind": "basis", "points_value": 0},
    ).json()

    instance = client.post(
        "/tasks/instances",
        headers=parent_a_headers,
        json={"template_id": template["id"], "assignee_user_id": str(child_a.id), "due_date": date.today().isoformat()},
    ).json()

    parent_b_headers = _register_parent(client, email="tabletB@example.ch")

    response = client.post(f"/tasks/instances/{instance['id']}/complete", headers=parent_b_headers)
    assert response.status_code == 404


def test_recurrence_rule_rejects_invalid_value(client, db_session):
    """Only 'daily' and 'weekly' are accepted recurrence rules."""
    parent_headers = _register_parent(client, email="invalid@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "invalid@example.ch"))
    child = _create_child(db_session, admin.family_id)

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Blumen gießen", "kind": "basis", "points_value": 0},
    ).json()

    response = client.post(
        "/tasks/instances",
        headers=parent_headers,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child.id),
            "due_date": date.today().isoformat(),
            "recurrence_rule": "monthly",
        },
    )

    assert response.status_code == 422
