import uuid as uuid_module
from datetime import date

from sqlalchemy import select

from app.core.security import hash_secret
from app.models.points import EarningsLedgerEntry
from app.models.task import TaskKind
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


def test_approved_basis_task_credits_zero_points(client, db_session):
    """Basis tasks are mandatory chores and never award points."""
    parent_headers = _register_parent(client)
    admin = db_session.scalar(select(User).where(User.email == "eltern@example.ch"))
    child = _create_child(db_session, admin.family_id)

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Zimmer aufräumen", "kind": "basis", "points_value": 0},
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

    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": str(child.id), "pin": "1234"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}

    complete = client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)
    assert complete.status_code == 200
    assert complete.json()["status"] == "completed"

    approve = client.post(f"/tasks/instances/{instance['id']}/approve", headers=parent_headers)
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"

    balance = client.get(
        "/points/balance", headers=parent_headers, params={"user_id": str(child.id)}
    ).json()
    assert balance["balance"] == 0


def test_geld_task_creates_pending_earning(client, db_session):
    parent_headers = _register_parent(client, email="geld@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "geld@example.ch"))
    child = _create_child(db_session, admin.family_id, pin="5678")

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Auto waschen", "kind": TaskKind.GELD.value, "money_value_chf": 10.0},
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

    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": str(child.id), "pin": "5678"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}
    client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)
    client.post(f"/tasks/instances/{instance['id']}/approve", headers=parent_headers)

    balance = client.get(
        "/earnings/balance", headers=parent_headers, params={"user_id": str(child.id)}
    ).json()
    assert balance["approved_chf"] == 10.0
    assert balance["paid_out_chf"] == 0.0


def test_bonus_task_applies_multiplier(client, db_session):
    parent_headers = _register_parent(client, email="bonus@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "bonus@example.ch"))
    child = _create_child(db_session, admin.family_id, pin="1111")

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Bonus-Aufgabe", "kind": "bonus", "points_value": 10, "bonus_multiplier": 1.5},
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

    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": str(child.id), "pin": "1111"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}

    complete = client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)
    assert complete.status_code == 200
    assert complete.json()["status"] == "completed"

    approve = client.post(f"/tasks/instances/{instance['id']}/approve", headers=parent_headers)
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"

    balance = client.get(
        "/points/balance", headers=parent_headers, params={"user_id": str(child.id)}
    ).json()
    assert balance["balance"] == 15


def test_basis_task_cannot_have_nonzero_points_value(client, db_session):
    """Basis tasks cannot be created with non-zero points_value."""
    parent_headers = _register_parent(client, email="basis@example.ch")

    response = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Basis-Aufgabe", "kind": "basis", "points_value": 10},
    )
    assert response.status_code == 422
    assert "Basis-Aufgaben sind Pflicht" in response.json()["detail"][0]["msg"]


def test_bonus_multiplier_not_allowed_for_basis_task(client, db_session):
    parent_headers = _register_parent(client, email="validation@example.ch")

    response = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Invalid", "kind": "basis", "points_value": 0, "bonus_multiplier": 2.0},
    )
    assert response.status_code == 422


def test_bonus_task_with_rounding(client, db_session):
    parent_headers = _register_parent(client, email="rounding@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "rounding@example.ch"))
    child = _create_child(db_session, admin.family_id, pin="3333")

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Rounding-Bonus", "kind": "bonus", "points_value": 5, "bonus_multiplier": 1.5},
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

    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": str(child.id), "pin": "3333"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}

    client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)
    client.post(f"/tasks/instances/{instance['id']}/approve", headers=parent_headers)

    balance = client.get(
        "/points/balance", headers=parent_headers, params={"user_id": str(child.id)}
    ).json()
    assert balance["balance"] == 8


def test_bonus_multiplier_negative_rejected(client, db_session):
    parent_headers = _register_parent(client, email="negative@example.ch")

    response = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Invalid Bonus", "kind": "bonus", "points_value": 10, "bonus_multiplier": -1},
    )
    assert response.status_code == 422


def test_bonus_multiplier_too_high_rejected(client, db_session):
    parent_headers = _register_parent(client, email="toolhigh@example.ch")

    response = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Invalid Bonus", "kind": "bonus", "points_value": 10, "bonus_multiplier": 10},
    )
    assert response.status_code == 422


def test_cross_family_ledger_read_blocked(client, db_session):
    """Cross-tenant read leak bug: family 2's parent cannot see family 1's ledger via user_id param."""
    parent_headers1 = _register_parent(client, email="family1@example.ch")
    parent_headers2 = _register_parent(client, email="family2@example.ch")

    admin1 = db_session.scalar(select(User).where(User.email == "family1@example.ch"))
    child1 = _create_child(db_session, admin1.family_id, pin="1111")

    # Family 1: create a geld task that will create a pending earnings entry
    template = client.post(
        "/tasks/templates",
        headers=parent_headers1,
        json={"title": "Auto waschen", "kind": "geld", "money_value_chf": 25.0},
    ).json()
    instance = client.post(
        "/tasks/instances",
        headers=parent_headers1,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child1.id),
            "due_date": date.today().isoformat(),
        },
    ).json()

    # Child1 completes the task
    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child1.family_id), "user_id": str(child1.id), "pin": "1111"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}
    client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)

    # Parent1 approves, creating an "approved" earnings entry
    client.post(f"/tasks/instances/{instance['id']}/approve", headers=parent_headers1)

    # Verify family 1's parent sees the ledger entry
    ledger1 = client.get(
        "/earnings/ledger",
        headers=parent_headers1,
        params={"user_id": str(child1.id)},
    ).json()
    assert len(ledger1) == 1
    assert ledger1[0]["amount_chf"] == 25.0

    # Family 2's parent tries to access family 1's child's ledger -> must get 404
    ledger2 = client.get(
        "/earnings/ledger",
        headers=parent_headers2,
        params={"user_id": str(child1.id)},
    )
    assert ledger2.status_code == 404
    assert "Nutzer nicht gefunden" in ledger2.json()["detail"]


def test_cross_family_balance_read_blocked(client, db_session):
    """Cross-tenant read leak bug: family 2's parent cannot see family 1's balance via user_id param."""
    parent_headers1 = _register_parent(client, email="family1b@example.ch")
    parent_headers2 = _register_parent(client, email="family2b@example.ch")

    admin1 = db_session.scalar(select(User).where(User.email == "family1b@example.ch"))
    child1 = _create_child(db_session, admin1.family_id, pin="1111")

    # Family 1: create and approve a geld task
    template = client.post(
        "/tasks/templates",
        headers=parent_headers1,
        json={"title": "Rasenmähen", "kind": "geld", "money_value_chf": 30.0},
    ).json()
    instance = client.post(
        "/tasks/instances",
        headers=parent_headers1,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child1.id),
            "due_date": date.today().isoformat(),
        },
    ).json()

    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child1.family_id), "user_id": str(child1.id), "pin": "1111"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}
    client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)
    client.post(f"/tasks/instances/{instance['id']}/approve", headers=parent_headers1)

    # Verify family 1's parent sees the balance
    balance1 = client.get(
        "/earnings/balance",
        headers=parent_headers1,
        params={"user_id": str(child1.id)},
    ).json()
    assert balance1["approved_chf"] == 30.0

    # Family 2's parent tries to access family 1's child's balance -> must get 404
    balance2 = client.get(
        "/earnings/balance",
        headers=parent_headers2,
        params={"user_id": str(child1.id)},
    )
    assert balance2.status_code == 404
    assert "Nutzer nicht gefunden" in balance2.json()["detail"]


def test_cross_family_approve_earning_blocked(client, db_session):
    """Cross-tenant bug: family 2's parent cannot approve family 1's earning."""
    parent_headers1 = _register_parent(client, email="family1c@example.ch")
    parent_headers2 = _register_parent(client, email="family2c@example.ch")

    admin1 = db_session.scalar(select(User).where(User.email == "family1c@example.ch"))
    child1 = _create_child(db_session, admin1.family_id, pin="1111")

    # Manually create a pending_approval earnings entry for family 1's child
    entry = EarningsLedgerEntry(
        id=uuid_module.uuid4(),
        user_id=child1.id,
        amount_chf=15.0,
        status="pending_approval",
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)
    entry_id = str(entry.id)

    # Verify entry exists in family 1
    ledger1 = client.get(
        "/earnings/ledger",
        headers=parent_headers1,
        params={"user_id": str(child1.id)},
    ).json()
    assert len(ledger1) == 1
    assert ledger1[0]["status"] == "pending_approval"

    # Family 2's parent tries to approve family 1's earning -> must get 404
    approve_response = client.post(
        f"/earnings/{entry_id}/approve",
        headers=parent_headers2,
    )
    assert approve_response.status_code == 404
    assert "Eintrag nicht gefunden" in approve_response.json()["detail"]

    # Verify the entry in family 1 is still pending_approval
    ledger1_after = client.get(
        "/earnings/ledger",
        headers=parent_headers1,
        params={"user_id": str(child1.id)},
    ).json()
    assert ledger1_after[0]["status"] == "pending_approval"


def test_cross_family_pay_out_earning_blocked(client, db_session):
    """Cross-tenant bug: family 2's parent cannot pay out family 1's earning."""
    parent_headers1 = _register_parent(client, email="family1d@example.ch")
    parent_headers2 = _register_parent(client, email="family2d@example.ch")

    admin1 = db_session.scalar(select(User).where(User.email == "family1d@example.ch"))
    child1 = _create_child(db_session, admin1.family_id, pin="1111")

    # Family 1: create, complete, and approve a geld task
    template = client.post(
        "/tasks/templates",
        headers=parent_headers1,
        json={"title": "Wäsche waschen", "kind": "geld", "money_value_chf": 20.0},
    ).json()
    instance = client.post(
        "/tasks/instances",
        headers=parent_headers1,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child1.id),
            "due_date": date.today().isoformat(),
        },
    ).json()

    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child1.family_id), "user_id": str(child1.id), "pin": "1111"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}
    client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)
    client.post(f"/tasks/instances/{instance['id']}/approve", headers=parent_headers1)

    # Get the earnings entry ID from ledger
    ledger1 = client.get(
        "/earnings/ledger",
        headers=parent_headers1,
        params={"user_id": str(child1.id)},
    ).json()
    entry_id = ledger1[0]["id"]
    assert ledger1[0]["status"] == "approved"

    # Family 2's parent tries to pay out family 1's earning -> must get 404
    payout_response = client.post(
        f"/earnings/{entry_id}/pay-out",
        headers=parent_headers2,
    )
    assert payout_response.status_code == 404
    assert "Kein auszahlbarer Eintrag gefunden" in payout_response.json()["detail"]

    # Verify the entry in family 1 is still "approved", not "paid_out"
    ledger1_after = client.get(
        "/earnings/ledger",
        headers=parent_headers1,
        params={"user_id": str(child1.id)},
    ).json()
    assert ledger1_after[0]["status"] == "approved"


def test_cross_family_points_balance_read_blocked(client, db_session):
    """Cross-tenant read leak bug: family 2's parent cannot read family 1's points balance via user_id param."""
    parent_headers1 = _register_parent(client, email="points_family1@example.ch")
    parent_headers2 = _register_parent(client, email="points_family2@example.ch")

    admin1 = db_session.scalar(select(User).where(User.email == "points_family1@example.ch"))
    child1 = _create_child(db_session, admin1.family_id, pin="1111")

    template = client.post(
        "/tasks/templates",
        headers=parent_headers1,
        json={"title": "Bonus-Aufgabe", "kind": "bonus", "points_value": 10, "bonus_multiplier": 1.0},
    ).json()
    instance = client.post(
        "/tasks/instances",
        headers=parent_headers1,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child1.id),
            "due_date": date.today().isoformat(),
        },
    ).json()

    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child1.family_id), "user_id": str(child1.id), "pin": "1111"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}
    client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)
    client.post(f"/tasks/instances/{instance['id']}/approve", headers=parent_headers1)

    balance1 = client.get(
        "/points/balance", headers=parent_headers1, params={"user_id": str(child1.id)}
    ).json()
    assert balance1["balance"] == 10

    balance2 = client.get(
        "/points/balance", headers=parent_headers2, params={"user_id": str(child1.id)}
    )
    assert balance2.status_code == 404
    assert "Nutzer nicht gefunden" in balance2.json()["detail"]


def test_cross_family_points_ledger_read_blocked(client, db_session):
    """Cross-tenant read leak bug: family 2's parent cannot read family 1's points ledger via user_id param."""
    parent_headers1 = _register_parent(client, email="points_ledger_f1@example.ch")
    parent_headers2 = _register_parent(client, email="points_ledger_f2@example.ch")

    admin1 = db_session.scalar(select(User).where(User.email == "points_ledger_f1@example.ch"))
    child1 = _create_child(db_session, admin1.family_id, pin="1111")

    template = client.post(
        "/tasks/templates",
        headers=parent_headers1,
        json={"title": "Bonus-Aufgabe", "kind": "bonus", "points_value": 10, "bonus_multiplier": 1.0},
    ).json()
    instance = client.post(
        "/tasks/instances",
        headers=parent_headers1,
        json={
            "template_id": template["id"],
            "assignee_user_id": str(child1.id),
            "due_date": date.today().isoformat(),
        },
    ).json()

    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child1.family_id), "user_id": str(child1.id), "pin": "1111"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}
    client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)
    client.post(f"/tasks/instances/{instance['id']}/approve", headers=parent_headers1)

    ledger1 = client.get(
        "/points/ledger", headers=parent_headers1, params={"user_id": str(child1.id)}
    ).json()
    assert len(ledger1) == 1

    ledger2 = client.get(
        "/points/ledger", headers=parent_headers2, params={"user_id": str(child1.id)}
    )
    assert ledger2.status_code == 404
    assert "Nutzer nicht gefunden" in ledger2.json()["detail"]


def test_same_family_approve_and_pay_out_happy_path(client, db_session):
    """Happy path: parent approves then pays out their own child's earning."""
    parent_headers = _register_parent(client, email="happy@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "happy@example.ch"))
    child = _create_child(db_session, admin.family_id, pin="1111")

    # Create and complete a geld task
    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Küche putzen", "kind": "geld", "money_value_chf": 50.0},
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

    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": str(child.id), "pin": "1111"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}
    client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)
    client.post(f"/tasks/instances/{instance['id']}/approve", headers=parent_headers)

    # Check initial balance: 50 CHF approved, 0 paid out
    balance1 = client.get(
        "/earnings/balance",
        headers=parent_headers,
        params={"user_id": str(child.id)},
    ).json()
    assert balance1["pending_chf"] == 0.0
    assert balance1["approved_chf"] == 50.0
    assert balance1["paid_out_chf"] == 0.0

    # Get entry and approve it
    ledger1 = client.get(
        "/earnings/ledger",
        headers=parent_headers,
        params={"user_id": str(child.id)},
    ).json()
    entry_id = ledger1[0]["id"]
    assert ledger1[0]["status"] == "approved"

    # Pay out the earning
    payout = client.post(
        f"/earnings/{entry_id}/pay-out",
        headers=parent_headers,
    )
    assert payout.status_code == 200
    assert payout.json()["status"] == "paid_out"

    # Check final balance: 0 approved, 50 CHF paid out
    balance2 = client.get(
        "/earnings/balance",
        headers=parent_headers,
        params={"user_id": str(child.id)},
    ).json()
    assert balance2["pending_chf"] == 0.0
    assert balance2["approved_chf"] == 0.0
    assert balance2["paid_out_chf"] == 50.0


def test_pay_out_non_approved_earning_fails(client, db_session):
    """Pay-out on pending_approval entry should fail (not approved yet)."""
    parent_headers = _register_parent(client, email="payoutfail@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "payoutfail@example.ch"))
    child = _create_child(db_session, admin.family_id, pin="1111")

    # Manually create a pending_approval earnings entry
    entry = EarningsLedgerEntry(
        id=uuid_module.uuid4(),
        user_id=child.id,
        amount_chf=40.0,
        status="pending_approval",
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)
    entry_id = str(entry.id)

    # Verify entry is pending_approval
    ledger = client.get(
        "/earnings/ledger",
        headers=parent_headers,
        params={"user_id": str(child.id)},
    ).json()
    assert len(ledger) == 1
    assert ledger[0]["status"] == "pending_approval"

    # Try to pay out without approving -> should fail
    payout = client.post(
        f"/earnings/{entry_id}/pay-out",
        headers=parent_headers,
    )
    assert payout.status_code == 404
    assert "Kein auszahlbarer Eintrag gefunden" in payout.json()["detail"]

    # Verify entry is still pending_approval
    ledger_after = client.get(
        "/earnings/ledger",
        headers=parent_headers,
        params={"user_id": str(child.id)},
    ).json()
    assert ledger_after[0]["status"] == "pending_approval"


def test_earnings_ledger_malformed_uuid_returns_422(client, db_session):
    """GET /earnings/ledger with malformed user_id returns 422, not 500."""
    parent_headers = _register_parent(client, email="malformed@example.ch")

    # Try to get ledger with malformed UUID
    response = client.get(
        "/earnings/ledger",
        headers=parent_headers,
        params={"user_id": "not-a-uuid"},
    )
    assert response.status_code == 422


def test_earnings_balance_malformed_uuid_returns_422(client, db_session):
    """GET /earnings/balance with malformed user_id returns 422, not 500."""
    parent_headers = _register_parent(client, email="malformed2@example.ch")

    # Try to get balance with malformed UUID
    response = client.get(
        "/earnings/balance",
        headers=parent_headers,
        params={"user_id": "not-a-uuid"},
    )
    assert response.status_code == 422


def test_list_instances_cross_family_blocked(client, db_session):
    """Cross-tenant read leak bug: family 2's parent cannot see family 1's task instances."""
    parent_headers1 = _register_parent(client, email="task_family1@example.ch")
    parent_headers2 = _register_parent(client, email="task_family2@example.ch")

    admin1 = db_session.scalar(select(User).where(User.email == "task_family1@example.ch"))
    child1 = _create_child(db_session, admin1.family_id, pin="1111")

    # Family 1: create and assign a task
    template1 = client.post(
        "/tasks/templates",
        headers=parent_headers1,
        json={"title": "Family 1 Task", "kind": "basis", "points_value": 0},
    ).json()
    instance1 = client.post(
        "/tasks/instances",
        headers=parent_headers1,
        json={
            "template_id": template1["id"],
            "assignee_user_id": str(child1.id),
            "due_date": date.today().isoformat(),
        },
    ).json()

    # Family 1's parent sees their own instance
    instances1 = client.get("/tasks/instances", headers=parent_headers1).json()
    assert len(instances1) == 1
    assert instances1[0]["id"] == instance1["id"]

    # Family 2's parent should NOT see family 1's instances
    instances2 = client.get("/tasks/instances", headers=parent_headers2).json()
    assert len(instances2) == 0


def test_list_instances_with_cross_family_assignee_param_blocked(client, db_session):
    """Cross-tenant bug: family 2's parent cannot see family 1's instances by specifying family 1's child."""
    parent_headers1 = _register_parent(client, email="task_param1@example.ch")
    parent_headers2 = _register_parent(client, email="task_param2@example.ch")

    admin1 = db_session.scalar(select(User).where(User.email == "task_param1@example.ch"))
    child1 = _create_child(db_session, admin1.family_id, pin="1111")

    # Family 1: create and assign a task
    template1 = client.post(
        "/tasks/templates",
        headers=parent_headers1,
        json={"title": "Family 1 Task", "kind": "basis", "points_value": 0},
    ).json()
    instance1 = client.post(
        "/tasks/instances",
        headers=parent_headers1,
        json={
            "template_id": template1["id"],
            "assignee_user_id": str(child1.id),
            "due_date": date.today().isoformat(),
        },
    ).json()

    # Verify family 1's parent can see the instance with assignee filter
    instances1 = client.get(
        "/tasks/instances",
        headers=parent_headers1,
        params={"assignee_user_id": str(child1.id)},
    ).json()
    assert len(instances1) == 1
    assert instances1[0]["id"] == instance1["id"]

    # Family 2's parent tries to use family 1's child as assignee_user_id param -> must get empty list
    instances2 = client.get(
        "/tasks/instances",
        headers=parent_headers2,
        params={"assignee_user_id": str(child1.id)},
    ).json()
    assert len(instances2) == 0


def test_approve_instance_cross_family_blocked(client, db_session):
    """Cross-tenant bug: family 2's parent cannot approve family 1's completed instance."""
    parent_headers1 = _register_parent(client, email="approve_family1@example.ch")
    parent_headers2 = _register_parent(client, email="approve_family2@example.ch")

    admin1 = db_session.scalar(select(User).where(User.email == "approve_family1@example.ch"))
    child1 = _create_child(db_session, admin1.family_id, pin="1111")

    # Family 1: create, assign, and complete a task
    template1 = client.post(
        "/tasks/templates",
        headers=parent_headers1,
        json={"title": "Family 1 Task", "kind": "basis", "points_value": 0},
    ).json()
    instance1 = client.post(
        "/tasks/instances",
        headers=parent_headers1,
        json={
            "template_id": template1["id"],
            "assignee_user_id": str(child1.id),
            "due_date": date.today().isoformat(),
        },
    ).json()

    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child1.family_id), "user_id": str(child1.id), "pin": "1111"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}
    client.post(f"/tasks/instances/{instance1['id']}/complete", headers=child_headers)

    # Family 2's parent tries to approve family 1's instance -> must get 404
    approve_response = client.post(
        f"/tasks/instances/{instance1['id']}/approve",
        headers=parent_headers2,
    )
    assert approve_response.status_code == 404
    assert "bestätigungsfähige Aufgabe" in approve_response.json()["detail"]

    # Verify family 1's instance is still "completed", not "approved"
    instance1_check = client.get(
        "/tasks/instances",
        headers=parent_headers1,
        params={"assignee_user_id": str(child1.id)},
    ).json()
    assert instance1_check[0]["status"] == "completed"

    # Verify no points ledger entry was created
    balance1 = client.get(
        "/points/balance",
        headers=parent_headers1,
        params={"user_id": str(child1.id)},
    ).json()
    assert balance1["balance"] == 0


def test_create_instance_cross_family_template_blocked(client, db_session):
    """Cross-tenant bug: family 2's parent cannot create instance using family 1's template."""
    parent_headers1 = _register_parent(client, email="create_template_f1@example.ch")
    parent_headers2 = _register_parent(client, email="create_template_f2@example.ch")

    admin2 = db_session.scalar(select(User).where(User.email == "create_template_f2@example.ch"))
    child2 = _create_child(db_session, admin2.family_id, pin="2222")

    # Family 1: create a template
    template1 = client.post(
        "/tasks/templates",
        headers=parent_headers1,
        json={"title": "Family 1 Template", "kind": "basis", "points_value": 0},
    ).json()

    # Family 2's parent tries to create instance with family 1's template and family 2's child -> must get 404
    create_response = client.post(
        "/tasks/instances",
        headers=parent_headers2,
        json={
            "template_id": template1["id"],
            "assignee_user_id": str(child2.id),
            "due_date": date.today().isoformat(),
        },
    )
    assert create_response.status_code == 404
    assert "Familienmitglied oder Aufgaben-Vorlage" in create_response.json()["detail"]


def test_create_instance_cross_family_child_blocked(client, db_session):
    """Cross-tenant bug: family 2's parent cannot create instance assigning family 1's child."""
    _register_parent(client, email="create_child_f1@example.ch")
    parent_headers2 = _register_parent(client, email="create_child_f2@example.ch")

    admin1 = db_session.scalar(select(User).where(User.email == "create_child_f1@example.ch"))
    child1 = _create_child(db_session, admin1.family_id, pin="1111")

    # Family 2: create a template
    template2 = client.post(
        "/tasks/templates",
        headers=parent_headers2,
        json={"title": "Family 2 Template", "kind": "basis", "points_value": 0},
    ).json()

    # Family 2's parent tries to create instance with family 2's template but family 1's child -> must get 404
    create_response = client.post(
        "/tasks/instances",
        headers=parent_headers2,
        json={
            "template_id": template2["id"],
            "assignee_user_id": str(child1.id),
            "due_date": date.today().isoformat(),
        },
    )
    assert create_response.status_code == 404
    assert "Familienmitglied oder Aufgaben-Vorlage" in create_response.json()["detail"]


def test_list_templates_family_scoped(client, db_session):
    """GET /tasks/templates should only return current family's templates."""
    parent_headers1 = _register_parent(client, email="list_templates_f1@example.ch")
    parent_headers2 = _register_parent(client, email="list_templates_f2@example.ch")

    # Family 1: create two templates
    client.post(
        "/tasks/templates",
        headers=parent_headers1,
        json={"title": "Family 1 Template 1", "kind": "basis", "points_value": 0},
    )
    client.post(
        "/tasks/templates",
        headers=parent_headers1,
        json={"title": "Family 1 Template 2", "kind": "bonus", "points_value": 10, "bonus_multiplier": 1.5},
    )

    # Family 2: create one template
    client.post(
        "/tasks/templates",
        headers=parent_headers2,
        json={"title": "Family 2 Template", "kind": "geld", "money_value_chf": 10.0},
    )

    # Family 1's parent sees only their templates
    templates1 = client.get("/tasks/templates", headers=parent_headers1).json()
    assert len(templates1) == 2
    assert all(t["title"].startswith("Family 1") for t in templates1)

    # Family 2's parent sees only their templates
    templates2 = client.get("/tasks/templates", headers=parent_headers2).json()
    assert len(templates2) == 1
    assert templates2[0]["title"] == "Family 2 Template"


def test_complete_instance_twice_before_approval_fails(client, db_session):
    """Completing an instance twice in a row (before approval) → second call returns 400, status stays COMPLETED."""
    parent_headers = _register_parent(client, email="double_complete@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "double_complete@example.ch"))
    child = _create_child(db_session, admin.family_id, pin="1234")

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Test Task", "kind": "basis", "points_value": 0},
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

    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": str(child.id), "pin": "1234"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}

    # First complete: should succeed
    complete1 = client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)
    assert complete1.status_code == 200
    assert complete1.json()["status"] == "completed"

    # Second complete: should fail with 400
    complete2 = client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)
    assert complete2.status_code == 400
    assert "bereits erledigt gemeldet oder freigegeben" in complete2.json()["detail"]

    # Verify status is still COMPLETED
    instance_check = client.get(
        "/tasks/instances",
        headers=parent_headers,
        params={"assignee_user_id": str(child.id)},
    ).json()
    assert instance_check[0]["status"] == "completed"


def test_complete_already_approved_instance_fails(client, db_session):
    """Completing an already-approved instance again → 400, no second PointsLedgerEntry created."""
    parent_headers = _register_parent(client, email="approve_then_complete@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "approve_then_complete@example.ch"))
    child = _create_child(db_session, admin.family_id, pin="1234")

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Points Task", "kind": "basis", "points_value": 0},
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

    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": str(child.id), "pin": "1234"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}

    # Complete the task
    complete = client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)
    assert complete.status_code == 200
    assert complete.json()["status"] == "completed"

    # Approve the task (no PointsLedgerEntry created for basis tasks)
    approve = client.post(f"/tasks/instances/{instance['id']}/approve", headers=parent_headers)
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"

    # Verify balance is still 0 (no entry created for basis tasks)
    balance1 = client.get(
        "/points/balance",
        headers=parent_headers,
        params={"user_id": str(child.id)},
    ).json()
    assert balance1["balance"] == 0

    # Try to complete the already-approved instance again: should fail with 400
    complete_again = client.post(f"/tasks/instances/{instance['id']}/complete", headers=child_headers)
    assert complete_again.status_code == 400
    assert "bereits erledigt gemeldet oder freigegeben" in complete_again.json()["detail"]

    # Verify balance is still 0 (no entry created)
    balance2 = client.get(
        "/points/balance",
        headers=parent_headers,
        params={"user_id": str(child.id)},
    ).json()
    assert balance2["balance"] == 0
