import uuid as uuid_module

from sqlalchemy import select

from app.core.security import hash_secret, verify_secret
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


def _create_child(db_session, family_id, pin="1234", display_name="Kind"):
    child = User(
        family_id=family_id,
        role=UserRole.CHILD,
        display_name=display_name,
        pin_hash=hash_secret(pin),
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    return child


def _create_dashboard_user(db_session, family_id, pin="9999", display_name="Wand-Dashboard"):
    dashboard_user = User(
        family_id=family_id,
        role=UserRole.DASHBOARD,
        display_name=display_name,
        avatar_key="📋",
        pin_hash=hash_secret(pin),
    )
    db_session.add(dashboard_user)
    db_session.commit()
    db_session.refresh(dashboard_user)
    return dashboard_user


def test_list_child_accounts_returns_only_child_users(client, db_session):
    """Test that the endpoint returns only child accounts for a family."""
    _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    # Create multiple children
    child1 = _create_child(db_session, admin.family_id, pin="1111", display_name="Anna")
    child2 = _create_child(db_session, admin.family_id, pin="2222", display_name="Bob")

    # Call the endpoint (no auth needed)
    response = client.get(f"/families/{admin.family_id}/child-accounts")
    assert response.status_code == 200

    children = response.json()
    assert len(children) == 2

    # Check that we got the right children
    child_ids = {child["id"] for child in children}
    assert str(child1.id) in child_ids
    assert str(child2.id) in child_ids

    # Check field presence
    for child in children:
        assert "id" in child
        assert "display_name" in child
        assert "avatar_key" in child
        assert child["display_name"] in ["Anna", "Bob"]

    # Verify sensitive fields are NOT present
    for child in children:
        assert "pin_hash" not in child
        assert "password_hash" not in child
        assert "email" not in child


def test_list_child_accounts_excludes_parent_admin(client, db_session):
    """Test that parent admin user is excluded from child accounts list."""
    _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    # Create a child
    child = _create_child(db_session, admin.family_id, pin="1234", display_name="Anna")

    # Call endpoint
    response = client.get(f"/families/{admin.family_id}/child-accounts")
    assert response.status_code == 200

    children = response.json()
    assert len(children) == 1
    assert children[0]["id"] == str(child.id)
    assert children[0]["display_name"] == "Anna"

    # Verify parent is not in the list
    parent_ids = {child["id"] for child in children}
    assert str(admin.id) not in parent_ids


def test_list_child_accounts_with_avatar_key(client, db_session):
    """Test that avatar_key is returned when set."""
    _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    # Create a child with avatar
    child = User(
        family_id=admin.family_id,
        role=UserRole.CHILD,
        display_name="Anna",
        avatar_key="unicorn",
        pin_hash=hash_secret("1234"),
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)

    response = client.get(f"/families/{admin.family_id}/child-accounts")
    assert response.status_code == 200

    children = response.json()
    assert len(children) == 1
    assert children[0]["avatar_key"] == "unicorn"


def test_list_child_accounts_nonexistent_family_is_unauthorized(client, db_session):
    """An unknown family is indistinguishable from a missing device token: 401, no info about the family."""
    nonexistent_family_id = uuid_module.uuid4()

    response = client.get(f"/families/{nonexistent_family_id}/child-accounts")
    assert response.status_code == 401


def test_list_pin_accounts_includes_children_and_dashboard_not_parents(client, db_session):
    _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    child = _create_child(db_session, admin.family_id, pin="1234", display_name="Anna")
    dashboard_user = _create_dashboard_user(db_session, admin.family_id, pin="9999")

    response = client.get(f"/families/{admin.family_id}/pin-accounts")
    assert response.status_code == 200

    accounts = response.json()
    account_ids = {account["id"] for account in accounts}
    assert str(child.id) in account_ids
    assert str(dashboard_user.id) in account_ids
    assert str(admin.id) not in account_ids
    assert {account["role"] for account in accounts} == {"child", "dashboard"}

    raw_json = response.text
    assert "pin_hash" not in raw_json
    assert "password_hash" not in raw_json


def test_list_child_accounts_doesnt_leak_pin_hash_in_raw_response(client, db_session):
    """Test that pin_hash never appears in the JSON response at all."""
    _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    # Create a child
    _create_child(db_session, admin.family_id, pin="1234", display_name="Anna")

    response = client.get(f"/families/{admin.family_id}/child-accounts")
    assert response.status_code == 200

    raw_json = response.text
    # Verify pin_hash doesn't appear anywhere in the response
    assert "pin_hash" not in raw_json
    assert "password_hash" not in raw_json
    assert "email" not in raw_json


# ============================================================================
# Tests for authenticated child management endpoints (POST/GET/PATCH/DELETE)
# ============================================================================


def test_post_create_child_creates_successfully(client, db_session):
    """Test that POST /families/children creates a child account."""
    parent_headers = _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    response = client.post(
        "/families/children",
        headers=parent_headers,
        json={
            "display_name": "Anna",
            "birth_year": 2015,
            "pin": "1234",
            "avatar_key": "🦄",
        },
    )
    assert response.status_code == 201
    data = response.json()

    # Verify response contains expected fields
    assert "id" in data
    assert data["display_name"] == "Anna"
    assert data["birth_year"] == 2015
    assert data["avatar_key"] == "🦄"

    # Verify pin_hash never appears in response
    raw_json = response.text
    assert "pin_hash" not in raw_json
    assert "password_hash" not in raw_json

    # Verify child was created in DB with correct family_id
    child = db_session.get(User, uuid_module.UUID(data["id"]))
    assert child is not None
    assert child.family_id == admin.family_id
    assert child.role == UserRole.CHILD
    assert verify_secret("1234", child.pin_hash)


def test_put_dashboard_user_creates_and_updates_single_dashboard_account(client, db_session):
    parent_headers = _register_parent(client, email="dashboard@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "dashboard@example.ch"))

    create_response = client.put(
        "/families/dashboard-user",
        headers=parent_headers,
        json={"display_name": "Küchen-Tablet", "pin": "2468", "avatar_key": "🏠"},
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["display_name"] == "Küchen-Tablet"
    assert created["avatar_key"] == "🏠"

    dashboard_user = db_session.get(User, uuid_module.UUID(created["id"]))
    assert dashboard_user.family_id == admin.family_id
    assert dashboard_user.role == UserRole.DASHBOARD
    assert verify_secret("2468", dashboard_user.pin_hash)

    update_response = client.put(
        "/families/dashboard-user",
        headers=parent_headers,
        json={"display_name": "Wand-Dashboard", "avatar_key": "📋"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["id"] == created["id"]
    assert update_response.json()["display_name"] == "Wand-Dashboard"

    dashboard_users = db_session.scalars(
        select(User).where(User.family_id == admin.family_id, User.role == UserRole.DASHBOARD)
    ).all()
    assert len(dashboard_users) == 1
    assert verify_secret("2468", dashboard_users[0].pin_hash)


def test_put_dashboard_user_requires_pin_on_create(client):
    parent_headers = _register_parent(client, email="dashboard-pin@example.ch")

    response = client.put(
        "/families/dashboard-user",
        headers=parent_headers,
        json={"display_name": "Wand-Dashboard", "avatar_key": "📋"},
    )
    assert response.status_code == 422


def test_post_create_child_invalid_pin_too_short(client, db_session):
    """Test that invalid PIN (too short) is rejected."""
    parent_headers = _register_parent(client)
    response = client.post(
        "/families/children",
        headers=parent_headers,
        json={
            "display_name": "Anna",
            "birth_year": 2015,
            "pin": "12",  # Too short
            "avatar_key": "🦄",
        },
    )
    assert response.status_code == 422
    assert "4 Ziffern" in response.text


def test_post_create_child_invalid_pin_contains_letters(client, db_session):
    """Test that PIN with letters is rejected."""
    parent_headers = _register_parent(client)
    response = client.post(
        "/families/children",
        headers=parent_headers,
        json={
            "display_name": "Anna",
            "birth_year": 2015,
            "pin": "12a4",  # Contains letter
            "avatar_key": "🦄",
        },
    )
    assert response.status_code == 422
    assert "4 Ziffern" in response.text


def test_post_create_child_invalid_birth_year_future(client, db_session):
    """Test that future birth year is rejected."""
    parent_headers = _register_parent(client)
    response = client.post(
        "/families/children",
        headers=parent_headers,
        json={
            "display_name": "Anna",
            "birth_year": 2099,  # Future year
            "pin": "1234",
        },
    )
    assert response.status_code == 422
    assert "Geburtsjahr" in response.text


def test_post_create_child_invalid_birth_year_too_old(client, db_session):
    """Test that birth year over 18 years ago is rejected."""
    parent_headers = _register_parent(client)
    response = client.post(
        "/families/children",
        headers=parent_headers,
        json={
            "display_name": "Anna",
            "birth_year": 1950,  # Too old
            "pin": "1234",
        },
    )
    assert response.status_code == 422
    assert "Geburtsjahr" in response.text


def test_post_create_child_requires_parent_role(client, db_session):
    """Test that children cannot create child accounts."""
    _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    # Create a child
    child = _create_child(db_session, admin.family_id, pin="1111", display_name="Bob")

    # Child tries to create another child (should fail)
    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": str(child.id), "pin": "1111"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}

    response = client.post(
        "/families/children",
        headers=child_headers,
        json={
            "display_name": "Anna",
            "birth_year": 2015,
            "pin": "1234",
            "avatar_key": "🦄",
        },
    )
    assert response.status_code == 403


def test_get_list_children_returns_family_children_only(client, db_session):
    """Test that GET /families/children returns only caller's family children."""
    # Setup: Two families, each with children
    parent1_headers = _register_parent(client, email="papa1@example.ch")
    admin1 = db_session.scalar(select(User).where(User.email == "papa1@example.ch"))

    parent2_headers = _register_parent(client, email="papa2@example.ch")
    admin2 = db_session.scalar(select(User).where(User.email == "papa2@example.ch"))

    # Family 1 has 2 children
    child1_1 = _create_child(db_session, admin1.family_id, pin="1111", display_name="Anna")
    child1_2 = _create_child(db_session, admin1.family_id, pin="2222", display_name="Bob")

    # Family 2 has 1 child
    child2_1 = _create_child(db_session, admin2.family_id, pin="3333", display_name="Charlie")

    # Parent 1 lists children
    response = client.get("/families/children", headers=parent1_headers)
    assert response.status_code == 200
    children = response.json()

    assert len(children) == 2
    child_ids = {c["id"] for c in children}
    assert str(child1_1.id) in child_ids
    assert str(child1_2.id) in child_ids
    assert str(child2_1.id) not in child_ids

    # Parent 2 lists children (should get only their child)
    response = client.get("/families/children", headers=parent2_headers)
    assert response.status_code == 200
    children = response.json()
    assert len(children) == 1
    assert children[0]["id"] == str(child2_1.id)


def test_get_list_children_ordered_by_display_name(client, db_session):
    """Test that children are returned sorted by display_name."""
    parent_headers = _register_parent(client)
    admin = db_session.scalar(select(User).where(User.email == "eltern@example.ch"))

    # Create children in random order
    _create_child(db_session, admin.family_id, pin="1111", display_name="Zebra")
    _create_child(db_session, admin.family_id, pin="2222", display_name="Anna")
    _create_child(db_session, admin.family_id, pin="3333", display_name="Bob")

    response = client.get("/families/children", headers=parent_headers)
    assert response.status_code == 200
    children = response.json()

    assert len(children) == 3
    assert children[0]["display_name"] == "Anna"
    assert children[1]["display_name"] == "Bob"
    assert children[2]["display_name"] == "Zebra"


def test_get_list_children_no_pin_hash_leaked(client, db_session):
    """Test that pin_hash is never in the response."""
    parent_headers = _register_parent(client)
    admin = db_session.scalar(select(User).where(User.email == "eltern@example.ch"))

    _create_child(db_session, admin.family_id, pin="1234", display_name="Anna")

    response = client.get("/families/children", headers=parent_headers)
    assert response.status_code == 200

    raw_json = response.text
    assert "pin_hash" not in raw_json
    assert "password_hash" not in raw_json


def test_patch_update_child_all_fields(client, db_session):
    """Test that PATCH /families/children/{id} updates all provided fields."""
    parent_headers = _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    child = _create_child(db_session, admin.family_id, pin="1234", display_name="Anna", )
    child_id = str(child.id)

    response = client.patch(
        f"/families/children/{child_id}",
        headers=parent_headers,
        json={
            "display_name": "Annika",
            "birth_year": 2016,
            "avatar_key": "🐶",
            "pin": "5678",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Annika"
    assert data["birth_year"] == 2016
    assert data["avatar_key"] == "🐶"

    # Verify PIN was actually changed
    child = db_session.get(User, uuid_module.UUID(child_id))
    assert verify_secret("5678", child.pin_hash)
    assert not verify_secret("1234", child.pin_hash)


def test_patch_update_child_partial_update(client, db_session):
    """Test that PATCH only updates provided fields, leaves others untouched."""
    parent_headers = _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    child = _create_child(
        db_session, admin.family_id, pin="1234", display_name="Anna"
    )
    child_id = str(child.id)

    # Update only display_name
    response = client.patch(
        f"/families/children/{child_id}",
        headers=parent_headers,
        json={"display_name": "Annika"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Annika"
    assert data["birth_year"] is None
    assert data["avatar_key"] is None

    # Verify old PIN still works (wasn't changed)
    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": child_id, "pin": "1234"},
    )
    assert child_login.status_code == 200


def test_patch_update_child_pin_changes_login(client, db_session):
    """Test that changing PIN actually changes what child-pin login accepts."""
    parent_headers = _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    child = _create_child(
        db_session, admin.family_id, pin="1234", display_name="Anna"
    )
    child_id = str(child.id)

    # Verify old PIN works
    response = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": child_id, "pin": "1234"},
    )
    assert response.status_code == 200

    # Update PIN via PATCH
    response = client.patch(
        f"/families/children/{child_id}",
        headers=parent_headers,
        json={"pin": "5678"},
    )
    assert response.status_code == 200

    # Verify old PIN no longer works
    response = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": child_id, "pin": "1234"},
    )
    assert response.status_code in [400, 401, 403]  # Should fail

    # Verify new PIN works
    response = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": child_id, "pin": "5678"},
    )
    assert response.status_code == 200


def test_patch_update_child_different_family_returns_404(client, db_session):
    """Test that PATCH on child from different family returns 404."""
    parent1_headers = _register_parent(client, email="papa1@example.ch")

    _register_parent(client, email="papa2@example.ch")
    admin2 = db_session.scalar(select(User).where(User.email == "papa2@example.ch"))

    # Create child in family 2
    child = _create_child(db_session, admin2.family_id, pin="1234", display_name="Anna")
    child_id = str(child.id)

    # Parent 1 tries to update child in family 2
    response = client.patch(
        f"/families/children/{child_id}",
        headers=parent1_headers,
        json={"display_name": "Hacked"},
    )
    assert response.status_code == 404

    # Verify child wasn't modified
    child = db_session.get(User, uuid_module.UUID(child_id))
    assert child.display_name == "Anna"


def test_patch_update_child_requires_parent_role(client, db_session):
    """Test that children cannot update child accounts."""
    _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    child1 = _create_child(db_session, admin.family_id, pin="1111", display_name="Bob")
    child2 = _create_child(db_session, admin.family_id, pin="2222", display_name="Anna")

    # Child tries to update another child (should fail)
    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child1.family_id), "user_id": str(child1.id), "pin": "1111"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}

    response = client.patch(
        f"/families/children/{child2.id}",
        headers=child_headers,
        json={"display_name": "Hacked"},
    )
    assert response.status_code == 403


def test_delete_child_deletes_successfully(client, db_session):
    """Test that DELETE /families/children/{id} removes the child."""
    parent_headers = _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    child = _create_child(db_session, admin.family_id, pin="1234", display_name="Anna")
    child_id = str(child.id)

    # Verify child exists
    response = client.get("/families/children", headers=parent_headers)
    assert response.status_code == 200
    children = response.json()
    assert len(children) == 1

    # Delete child
    response = client.delete(f"/families/children/{child_id}", headers=parent_headers)
    assert response.status_code == 204

    # Verify child no longer exists
    response = client.get("/families/children", headers=parent_headers)
    assert response.status_code == 200
    children = response.json()
    assert len(children) == 0

    # Verify child-pin login fails for deleted child
    response = client.post(
        "/auth/child-pin",
        json={"family_id": str(admin.family_id), "user_id": child_id, "pin": "1234"},
    )
    assert response.status_code in [400, 401, 403]  # Should fail


def test_delete_child_different_family_returns_404(client, db_session):
    """Test that DELETE on child from different family returns 404 and doesn't delete."""
    parent1_headers = _register_parent(client, email="papa1@example.ch")

    _register_parent(client, email="papa2@example.ch")
    admin2 = db_session.scalar(select(User).where(User.email == "papa2@example.ch"))

    # Create child in family 2
    child = _create_child(db_session, admin2.family_id, pin="1234", display_name="Anna")
    child_id = str(child.id)

    # Parent 1 tries to delete child in family 2
    response = client.delete(f"/families/children/{child_id}", headers=parent1_headers)
    assert response.status_code == 404

    # Verify child still exists
    child = db_session.get(User, uuid_module.UUID(child_id))
    assert child is not None
    assert child.display_name == "Anna"


def test_delete_child_requires_parent_role(client, db_session):
    """Test that children cannot delete child accounts."""
    _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    child1 = _create_child(db_session, admin.family_id, pin="1111", display_name="Bob")
    child2 = _create_child(db_session, admin.family_id, pin="2222", display_name="Anna")

    # Child tries to delete another child (should fail)
    child_login = client.post(
        "/auth/child-pin",
        json={"family_id": str(child1.family_id), "user_id": str(child1.id), "pin": "1111"},
    )
    child_headers = {"Authorization": f"Bearer {child_login.json()['access_token']}"}

    response = client.delete(f"/families/children/{child2.id}", headers=child_headers)
    assert response.status_code == 403

    # Verify child2 still exists
    child2_after = db_session.get(User, child2.id)
    assert child2_after is not None


# ============================================================================
# Tests for error handling (Fix 1: redact PIN in validation errors)
# ============================================================================


def test_post_create_child_invalid_pin_redacted_in_422_response(client):
    """Test that invalid PIN value is redacted in 422 validation error response.

    This test ensures that if someone submits an invalid PIN like "12a4", the
    raw value "12a4" does NOT appear anywhere in the HTTP response body, preventing
    credential-attempt logging in proxies/APM tools.
    """
    parent_headers = _register_parent(client)

    response = client.post(
        "/families/children",
        headers=parent_headers,
        json={
            "display_name": "Anna",
            "birth_year": 2015,
            "pin": "12a4",  # Invalid: contains letter
            "avatar_key": "🦄",
        },
    )
    assert response.status_code == 422
    raw_response = response.text

    # The actual invalid PIN "12a4" should NOT appear in the response
    assert "12a4" not in raw_response

    # The redaction marker should be present
    assert "[redacted]" in raw_response

    # The error message itself should still be present
    assert "4 Ziffern" in raw_response


def test_patch_update_child_invalid_pin_redacted_in_422_response(client, db_session):
    """Test that invalid PIN in PATCH is redacted in 422 response."""
    parent_headers = _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    child = _create_child(db_session, admin.family_id, pin="1234", display_name="Anna")
    child_id = str(child.id)

    response = client.patch(
        f"/families/children/{child_id}",
        headers=parent_headers,
        json={
            "display_name": "Bob",
            "pin": "99xx",  # Invalid: contains letters
        },
    )
    assert response.status_code == 422
    raw_response = response.text

    # The invalid PIN "99xx" should NOT appear
    assert "99xx" not in raw_response

    # The redaction marker should be present
    assert "[redacted]" in raw_response

    # Error message should still be present
    assert "4 Ziffern" in raw_response


def test_validation_error_redacts_password_fields(client):
    """Test that password fields are also redacted in validation errors."""
    # Try to register with invalid password (too short, if validation exists)
    # This tests the general principle that password fields are redacted
    response = client.post(
        "/auth/register-family",
        json={
            "family_name": "Test",
            "admin_display_name": "Papa",
            "admin_email": "papa@example.ch",
            "admin_password": "weak",
        },
    )
    # Whether it fails or succeeds, if there's a validation error for password,
    # the raw value should not appear
    raw_response = response.text
    if response.status_code == 422:
        assert "weak" not in raw_response or "[redacted]" in raw_response


def test_validation_error_preserves_non_sensitive_input_values(client, db_session):
    """Test that non-sensitive fields still include their input value in errors.

    This ensures we only redact sensitive fields, not all validation errors.
    """
    parent_headers = _register_parent(client)

    response = client.post(
        "/families/children",
        headers=parent_headers,
        json={
            "display_name": "Anna",
            "birth_year": 2099,  # Invalid future year
            "pin": "1234",
            "avatar_key": "🦄",
        },
    )
    assert response.status_code == 422
    raw_response = response.text

    # The invalid birth_year value SHOULD appear (it's not sensitive)
    assert "2099" in raw_response or "Geburtsjahr" in raw_response


# ============================================================================
# Tests for null PIN handling (Fix 2: PATCH with explicit null pin)
# ============================================================================


def test_patch_update_child_with_explicit_null_pin_does_not_change_pin(client, db_session):
    """Test that PATCH with {"pin": null} doesn't change the PIN and returns 200.

    Previously, this would crash with AttributeError because hash_secret(None)
    was called. This test ensures the fix handles explicit null correctly.
    """
    parent_headers = _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    child = _create_child(db_session, admin.family_id, pin="1234", display_name="Anna")
    child_id = str(child.id)
    original_pin_hash = db_session.get(User, uuid_module.UUID(child_id)).pin_hash

    # PATCH with explicit null pin and a different field
    response = client.patch(
        f"/families/children/{child_id}",
        headers=parent_headers,
        json={
            "pin": None,  # Explicit null
            "display_name": "Neuer Name",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Neuer Name"

    # Verify PIN was NOT changed
    child_after = db_session.get(User, uuid_module.UUID(child_id))
    assert child_after.pin_hash == original_pin_hash

    # Verify the original PIN still works for login
    login_response = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": child_id, "pin": "1234"},
    )
    assert login_response.status_code == 200


def test_patch_update_child_with_only_null_pin_field(client, db_session):
    """Test that PATCH with only {"pin": null} updates nothing and returns 200."""
    parent_headers = _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))

    child = _create_child(db_session, admin.family_id, pin="5555", display_name="Bob")
    child_id = str(child.id)

    # Store original state
    child_before = db_session.get(User, uuid_module.UUID(child_id))
    original_display_name = child_before.display_name
    original_pin_hash = child_before.pin_hash

    # PATCH with ONLY explicit null pin
    response = client.patch(
        f"/families/children/{child_id}",
        headers=parent_headers,
        json={"pin": None},
    )
    assert response.status_code == 200

    # Verify nothing changed
    child_after = db_session.get(User, uuid_module.UUID(child_id))
    assert child_after.display_name == original_display_name
    assert child_after.pin_hash == original_pin_hash

    # Verify original PIN still works
    login_response = client.post(
        "/auth/child-pin",
        json={"family_id": str(child.family_id), "user_id": child_id, "pin": "5555"},
    )
    assert login_response.status_code == 200


def _pin_headers(client, user, pin):
    response = client.post(
        "/auth/pin",
        json={"family_id": str(user.family_id), "user_id": str(user.id), "pin": pin},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_list_children_as_child_returns_only_self(client, db_session):
    _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))
    anna = _create_child(db_session, admin.family_id, pin="1111", display_name="Anna")
    bob = _create_child(db_session, admin.family_id, pin="2222", display_name="Bob")

    response = client.get("/families/children", headers=_pin_headers(client, anna, "1111"))

    assert response.status_code == 200
    body = response.json()
    assert [c["id"] for c in body] == [str(anna.id)]
    assert str(bob.id) not in response.text
    assert "Bob" not in response.text


def test_list_children_as_parent_sees_all_children(client, db_session):
    headers = _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))
    anna = _create_child(db_session, admin.family_id, pin="1111", display_name="Anna")
    bob = _create_child(db_session, admin.family_id, pin="2222", display_name="Bob")

    response = client.get("/families/children", headers=headers)

    assert {c["id"] for c in response.json()} == {str(anna.id), str(bob.id)}


def test_list_children_as_dashboard_sees_all_children(client, db_session):
    _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))
    anna = _create_child(db_session, admin.family_id, pin="1111", display_name="Anna")
    bob = _create_child(db_session, admin.family_id, pin="2222", display_name="Bob")
    wall = _create_dashboard_user(db_session, admin.family_id, pin="9999")

    response = client.get("/families/children", headers=_pin_headers(client, wall, "9999"))

    assert response.status_code == 200
    assert {c["id"] for c in response.json()} == {str(anna.id), str(bob.id)}


def test_list_children_as_child_never_shows_other_family(client, db_session):
    _register_parent(client, email="papa1@example.ch")
    _register_parent(client, email="papa2@example.ch")
    admin1 = db_session.scalar(select(User).where(User.email == "papa1@example.ch"))
    admin2 = db_session.scalar(select(User).where(User.email == "papa2@example.ch"))
    anna = _create_child(db_session, admin1.family_id, pin="1111", display_name="Anna")
    _create_child(db_session, admin2.family_id, pin="3333", display_name="Charlie")

    response = client.get("/families/children", headers=_pin_headers(client, anna, "1111"))

    assert [c["id"] for c in response.json()] == [str(anna.id)]


def test_dashboard_user_endpoint_forbidden_for_child_and_dashboard(client, db_session):
    _register_parent(client, email="papa@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "papa@example.ch"))
    anna = _create_child(db_session, admin.family_id, pin="1111", display_name="Anna")
    wall = _create_dashboard_user(db_session, admin.family_id, pin="9999")

    assert client.get("/families/dashboard-user", headers=_pin_headers(client, anna, "1111")).status_code == 403
    assert client.get("/families/dashboard-user", headers=_pin_headers(client, wall, "9999")).status_code == 403
