"""Rewards catalog tests following TDD approach."""
from sqlalchemy import select

from app.core.security import hash_secret
from app.models.points import PointsLedgerEntry
from app.models.user import User, UserRole


def _register_parent(client, email="eltern@example.ch"):
    response = client.post(
        "/auth/register-family",
        json={
            "family_name": "Muster",
            "admin_display_name": "Elternteil",
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


def test_parent_creates_reward_and_get_returns_it(client):
    """Parent creates a reward, GET /rewards returns it."""
    headers = _register_parent(client)

    response = client.post(
        "/rewards",
        headers=headers,
        json={"title": "1 Stunde Bildschirmzeit", "points_cost": 10},
    )
    assert response.status_code == 201
    reward = response.json()
    assert reward["title"] == "1 Stunde Bildschirmzeit"
    assert reward["points_cost"] == 10
    assert "id" in reward

    # GET /rewards returns the created reward
    list_response = client.get("/rewards", headers=headers)
    assert list_response.status_code == 200
    rewards = list_response.json()
    assert len(rewards) == 1
    assert rewards[0]["id"] == reward["id"]
    assert rewards[0]["title"] == "1 Stunde Bildschirmzeit"


def test_reward_icon_is_optional_and_round_trips(client):
    """A reward's icon, when given, comes back unchanged; omitting it defaults to None."""
    headers = _register_parent(client, email="icon@example.ch")

    with_icon = client.post(
        "/rewards", headers=headers, json={"title": "Kino-Abend", "points_cost": 20, "icon": "🎬"}
    )
    assert with_icon.status_code == 201
    assert with_icon.json()["icon"] == "🎬"

    without_icon = client.post(
        "/rewards", headers=headers, json={"title": "Eis essen", "points_cost": 5}
    )
    assert without_icon.status_code == 201
    assert without_icon.json()["icon"] is None


def test_get_rewards_ordered_by_points_cost_ascending(client):
    """GET /rewards returns rewards ordered by points_cost (cheapest first)."""
    headers = _register_parent(client)

    # Create rewards in random order
    client.post(
        "/rewards",
        headers=headers,
        json={"title": "Teuer", "points_cost": 100},
    )

    client.post(
        "/rewards",
        headers=headers,
        json={"title": "Günstig", "points_cost": 5},
    )

    client.post(
        "/rewards",
        headers=headers,
        json={"title": "Mittel", "points_cost": 50},
    )

    # GET /rewards should return them ordered by points_cost ascending
    list_response = client.get("/rewards", headers=headers)
    rewards = list_response.json()
    assert len(rewards) == 3
    assert rewards[0]["points_cost"] == 5
    assert rewards[1]["points_cost"] == 50
    assert rewards[2]["points_cost"] == 100


def test_get_rewards_filters_by_family(client, db_session):
    """GET /rewards for family 2 should NOT include family 1's rewards."""
    headers1 = _register_parent(client, email="family1@example.ch")
    headers2 = _register_parent(client, email="family2@example.ch")

    # Family 1 creates a reward
    client.post(
        "/rewards",
        headers=headers1,
        json={"title": "Familie 1 Belohnung", "points_cost": 10},
    )

    # Family 2 creates a reward
    client.post(
        "/rewards",
        headers=headers2,
        json={"title": "Familie 2 Belohnung", "points_cost": 10},
    )

    # Family 1 should only see their reward
    list1 = client.get("/rewards", headers=headers1).json()
    assert len(list1) == 1
    assert list1[0]["title"] == "Familie 1 Belohnung"

    # Family 2 should only see their reward
    list2 = client.get("/rewards", headers=headers2).json()
    assert len(list2) == 1
    assert list2[0]["title"] == "Familie 2 Belohnung"


def test_child_redeems_reward_with_sufficient_points(client, db_session):
    """Child with enough points successfully redeems a reward."""
    parent_headers = _register_parent(client, email="parent@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "parent@example.ch"))
    child = _create_child(db_session, admin.family_id)

    # Parent creates a reward
    reward = client.post(
        "/rewards",
        headers=parent_headers,
        json={"title": "Eiskrem", "points_cost": 10},
    ).json()

    # Give child 10 points via ledger entry
    db_session.add(
        PointsLedgerEntry(
            user_id=child.id,
            delta=10,
            reason="task_completed",
        )
    )
    db_session.commit()

    # Verify child has 10 points
    balance_response = client.get(
        "/points/balance",
        params={"user_id": str(child.id)},
        headers=parent_headers,
    ).json()
    assert balance_response["balance"] == 10

    # Child logs in
    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": str(child.id), "pin": "1234"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}

    # Child redeems the reward
    redeem_response = client.post(
        f"/rewards/{reward['id']}/redeem",
        headers=child_headers,
    )
    assert redeem_response.status_code == 200
    data = redeem_response.json()
    assert data["redeemed"] == "Eiskrem"
    assert data["remaining_balance"] == 0

    # Verify balance is now 0
    new_balance = client.get(
        "/points/balance",
        params={"user_id": str(child.id)},
        headers=parent_headers,
    ).json()
    assert new_balance["balance"] == 0


def test_child_redeem_with_insufficient_points_fails(client, db_session):
    """Redeeming with insufficient points returns 400 and does not create ledger entry."""
    parent_headers = _register_parent(client, email="parent@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "parent@example.ch"))
    child = _create_child(db_session, admin.family_id)

    # Parent creates a reward costing 50 points
    reward = client.post(
        "/rewards",
        headers=parent_headers,
        json={"title": "Teuer", "points_cost": 50},
    ).json()

    # Give child only 10 points
    db_session.add(
        PointsLedgerEntry(
            user_id=child.id,
            delta=10,
            reason="task_completed",
        )
    )
    db_session.commit()

    # Child logs in
    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": str(child.id), "pin": "1234"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}

    # Child tries to redeem (should fail)
    redeem_response = client.post(
        f"/rewards/{reward['id']}/redeem",
        headers=child_headers,
    )
    assert redeem_response.status_code == 400
    assert "Nicht genug Punkte" in redeem_response.json()["detail"]

    # Verify balance is still 10 (no ledger entry created)
    balance = client.get(
        "/points/balance",
        params={"user_id": str(child.id)},
        headers=parent_headers,
    ).json()
    assert balance["balance"] == 10


def test_cross_tenant_redeem_is_blocked(client, db_session):
    """Cross-tenant redeem: family 2's user cannot redeem family 1's reward."""
    parent_headers1 = _register_parent(client, email="family1@example.ch")
    parent_headers2 = _register_parent(client, email="family2@example.ch")

    admin2 = db_session.scalar(select(User).where(User.email == "family2@example.ch"))

    child2 = _create_child(db_session, admin2.family_id, pin="2222")

    # Family 1 creates a reward
    reward = client.post(
        "/rewards",
        headers=parent_headers1,
        json={"title": "Familie 1 Reward", "points_cost": 10},
    ).json()

    # Give family 2's child 10 points
    db_session.add(
        PointsLedgerEntry(
            user_id=child2.id,
            delta=10,
            reason="task_completed",
        )
    )
    db_session.commit()

    # Child from family 2 logs in
    child2_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child2.family_id), "user_id": str(child2.id), "pin": "2222"},
    )
    child2_headers = {"Authorization": f"Bearer {child2_login.json()['access_token']}"}

    # Try to redeem family 1's reward (should fail with 404)
    redeem_response = client.post(
        f"/rewards/{reward['id']}/redeem",
        headers=child2_headers,
    )
    assert redeem_response.status_code == 404
    assert "Belohnung nicht gefunden" in redeem_response.json()["detail"]

    # Verify no ledger entry was created for child2
    balance = client.get(
        "/points/balance",
        params={"user_id": str(child2.id)},
        headers=parent_headers2,
    ).json()
    assert balance["balance"] == 10


def test_reward_with_zero_or_negative_points_cost_rejected(client):
    """Creating reward with points_cost <= 0 returns 422."""
    headers = _register_parent(client)

    # Try points_cost = 0
    response = client.post(
        "/rewards",
        headers=headers,
        json={"title": "Kostenlos", "points_cost": 0},
    )
    assert response.status_code == 422

    # Try points_cost = -5
    response = client.post(
        "/rewards",
        headers=headers,
        json={"title": "Negative", "points_cost": -5},
    )
    assert response.status_code == 422


def test_child_cannot_create_reward(client, db_session):
    """Non-parent (child) calling POST /rewards returns 403."""
    _register_parent(client, email="parent@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "parent@example.ch"))
    child = _create_child(db_session, admin.family_id)

    # Child logs in
    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": str(child.id), "pin": "1234"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}

    # Child tries to create a reward (should fail with 403)
    response = client.post(
        "/rewards",
        headers=child_headers,
        json={"title": "Meine Belohnung", "points_cost": 10},
    )
    assert response.status_code == 403
    assert "Nur für Elternteile" in response.json()["detail"]
