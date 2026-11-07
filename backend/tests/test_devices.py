"""Device pairing: only devices a parent approved may show the children and do PIN logins."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.deps import hash_device_token
from app.core.security import hash_secret
from app.models.family_device import FamilyDevice
from app.models.user import User, UserRole


def _register(client, email, family_name="Muster"):
    response = client.post(
        "/auth/register-family",
        json={
            "family_name": family_name,
            "admin_display_name": "Papa",
            "admin_email": email,
            "admin_password": "s3hr-sicher",
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _user(db, email):
    return db.scalar(select(User).where(User.email == email))


def _child(db, family_id, name="Anna", pin="1234", role=UserRole.CHILD):
    user = User(family_id=family_id, role=role, display_name=name, pin_hash=hash_secret(pin))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _pair(client, headers, label="Tablet"):
    response = client.post("/families/devices", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()


def test_pairing_lifecycle_token_shown_once_and_stored_hashed(unpaired_client, db_session):
    parent = _register(unpaired_client, "a@example.ch")
    paired = _pair(unpaired_client, parent, "Küchen-Tablet")

    assert paired["label"] == "Küchen-Tablet"
    assert len(paired["device_token"]) >= 40
    device = db_session.get(FamilyDevice, uuid.UUID(paired["id"]))
    assert device.token_hash == hash_device_token(paired["device_token"])
    assert device.token_hash != paired["device_token"]
    assert paired["device_token"] not in str(device.__dict__.values())
    delta = device.expires_at - datetime.now(UTC)
    assert timedelta(days=364) < delta <= timedelta(days=365)

    listing = unpaired_client.get("/families/devices", headers=parent)
    assert listing.status_code == 200
    rows = listing.json()
    assert [r["label"] for r in rows] == ["Küchen-Tablet"]
    assert rows[0]["revoked"] is False
    assert "device_token" not in listing.text and "token_hash" not in listing.text
    assert paired["device_token"] not in listing.text


def test_device_endpoints_are_parent_only(unpaired_client, db_session):
    parent = _register(unpaired_client, "a@example.ch")
    family_id = _user(db_session, "a@example.ch").family_id
    child = _child(db_session, family_id)
    token = _pair(unpaired_client, parent)["device_token"]
    child_login = unpaired_client.post(
        "/auth/pin",
        headers={"X-Device-Token": token},
        json={"family_id": str(family_id), "user_id": str(child.id), "pin": "1234"},
    ).json()["access_token"]
    child_headers = {"Authorization": f"Bearer {child_login}"}

    assert unpaired_client.post("/families/devices", headers=child_headers, json={"label": "x"}).status_code == 403
    assert unpaired_client.get("/families/devices", headers=child_headers).status_code == 403
    assert unpaired_client.delete(f"/families/devices/{uuid.uuid4()}", headers=child_headers).status_code == 403
    assert unpaired_client.post("/families/devices", json={"label": "x"}).status_code == 401


def test_label_is_validated(unpaired_client):
    parent = _register(unpaired_client, "a@example.ch")
    for label in ("", "   ", "x" * 61):
        assert unpaired_client.post("/families/devices", headers=parent, json={"label": label}).status_code == 422


def test_cap_of_ten_active_devices_and_revoked_do_not_count(unpaired_client):
    parent = _register(unpaired_client, "a@example.ch")
    paired = [_pair(unpaired_client, parent, f"T{i}") for i in range(10)]
    over = unpaired_client.post("/families/devices", headers=parent, json={"label": "elftes"})
    assert over.status_code == 409

    assert unpaired_client.delete(f"/families/devices/{paired[0]['id']}", headers=parent).status_code == 204
    assert unpaired_client.post("/families/devices", headers=parent, json={"label": "elftes"}).status_code == 201


def test_revoke_marks_device_and_token_stops_working(unpaired_client, db_session):
    parent = _register(unpaired_client, "a@example.ch")
    family_id = _user(db_session, "a@example.ch").family_id
    _child(db_session, family_id)
    paired = _pair(unpaired_client, parent)
    url = f"/families/{family_id}/pin-accounts"
    header = {"X-Device-Token": paired["device_token"]}
    assert unpaired_client.get(url, headers=header).status_code == 200

    assert unpaired_client.delete(f"/families/devices/{paired['id']}", headers=parent).status_code == 204

    assert unpaired_client.get(url, headers=header).status_code == 401
    rows = unpaired_client.get("/families/devices", headers=parent).json()
    assert rows[0]["revoked"] is True
    # revoking again stays idempotent
    assert unpaired_client.delete(f"/families/devices/{paired['id']}", headers=parent).status_code == 204


def test_devices_are_tenant_scoped(unpaired_client, db_session):
    parent_a = _register(unpaired_client, "a@example.ch", "A")
    parent_b = _register(unpaired_client, "b@example.ch", "B")
    paired_b = _pair(unpaired_client, parent_b, "B-Tablet")

    assert unpaired_client.get("/families/devices", headers=parent_a).json() == []
    assert unpaired_client.delete(f"/families/devices/{paired_b['id']}", headers=parent_a).status_code == 404
    assert unpaired_client.delete(f"/families/devices/{uuid.uuid4()}", headers=parent_a).status_code == 404
    # B's device is untouched
    assert db_session.get(FamilyDevice, uuid.UUID(paired_b["id"])).revoked_at is None


def test_cap_is_per_family(unpaired_client):
    parent_a = _register(unpaired_client, "a@example.ch", "A")
    parent_b = _register(unpaired_client, "b@example.ch", "B")
    for i in range(10):
        _pair(unpaired_client, parent_a, f"T{i}")
    assert unpaired_client.post("/families/devices", headers=parent_b, json={"label": "ok"}).status_code == 201


def test_last_used_at_is_updated_at_most_every_ten_minutes(unpaired_client, db_session):
    parent = _register(unpaired_client, "a@example.ch")
    family_id = _user(db_session, "a@example.ch").family_id
    paired = _pair(unpaired_client, parent)
    device = db_session.get(FamilyDevice, uuid.UUID(paired["id"]))
    assert device.last_used_at is None
    url = f"/families/{family_id}/pin-accounts"
    header = {"X-Device-Token": paired["device_token"]}

    unpaired_client.get(url, headers=header)
    db_session.refresh(device)
    first = device.last_used_at
    assert first is not None

    unpaired_client.get(url, headers=header)
    db_session.refresh(device)
    assert device.last_used_at == first  # within 10 minutes: untouched

    device.last_used_at = datetime.now(UTC) - timedelta(minutes=11)
    db_session.commit()
    unpaired_client.get(url, headers=header)
    db_session.refresh(device)
    assert device.last_used_at > first


def _all_failure_responses(unpaired_client, db_session):
    """Requests on pin-accounts/auth-pin that must all yield the identical 401."""
    parent_a = _register(unpaired_client, "a@example.ch", "A")
    parent_b = _register(unpaired_client, "b@example.ch", "B")
    family_a = _user(db_session, "a@example.ch").family_id
    family_b = _user(db_session, "b@example.ch").family_id
    child_a = _child(db_session, family_a)
    token_a = _pair(unpaired_client, parent_a)["device_token"]
    token_b = _pair(unpaired_client, parent_b)["device_token"]
    revoked = _pair(unpaired_client, parent_a, "alt")
    unpaired_client.delete(f"/families/devices/{revoked['id']}", headers=parent_a)
    expired = _pair(unpaired_client, parent_a, "abgelaufen")
    device = db_session.get(FamilyDevice, uuid.UUID(expired["id"]))
    device.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    tokens = {
        "missing": None,
        "garbage": "not-a-token",
        "other family": token_b,
        "revoked": revoked["device_token"],
        "expired": expired["device_token"],
    }
    results = {}
    for name, token in tokens.items():
        headers = {"X-Device-Token": token} if token else {}
        results[f"pin-accounts {name}"] = unpaired_client.get(f"/families/{family_a}/pin-accounts", headers=headers)
        results[f"auth-pin {name}"] = unpaired_client.post(
            "/auth/pin",
            headers=headers,
            json={"family_id": str(family_a), "user_id": str(child_a.id), "pin": "1234"},
        )
        results[f"child-pin {name}"] = unpaired_client.post(
            "/auth/child-pin",
            headers=headers,
            json={"family_id": str(family_a), "user_id": str(child_a.id), "pin": "1234"},
        )
    # valid token of A, but asking for the unknown family / for B's family
    header_a = {"X-Device-Token": token_a}
    unknown_url = f"/families/{uuid.uuid4()}/pin-accounts"
    results["pin-accounts unknown family"] = unpaired_client.get(unknown_url, headers=header_a)
    results["pin-accounts foreign family"] = unpaired_client.get(f"/families/{family_b}/pin-accounts", headers=header_a)
    results["auth-pin foreign family"] = unpaired_client.post(
        "/auth/pin",
        headers=header_a,
        json={"family_id": str(family_b), "user_id": str(child_a.id), "pin": "1234"},
    )
    results["auth-pin unknown family"] = unpaired_client.post(
        "/auth/pin",
        headers=header_a,
        json={"family_id": str(uuid.uuid4()), "user_id": str(child_a.id), "pin": "1234"},
    )
    return results


def test_pin_accounts_and_pin_login_return_identical_401_for_every_failure(unpaired_client, db_session):
    results = _all_failure_responses(unpaired_client, db_session)
    bodies = set()
    for name, response in results.items():
        assert response.status_code == 401, name
        bodies.add(response.text)
        assert "Anna" not in response.text, name
    assert len(bodies) == 1, bodies


def test_child_accounts_without_credentials_is_the_same_401(unpaired_client, db_session):
    _register(unpaired_client, "a@example.ch")
    family_id = _user(db_session, "a@example.ch").family_id
    _child(db_session, family_id)
    real = unpaired_client.get(f"/families/{family_id}/child-accounts")
    unknown = unpaired_client.get(f"/families/{uuid.uuid4()}/child-accounts")
    garbage = unpaired_client.get(f"/families/{family_id}/child-accounts", headers={"X-Device-Token": "nope"})
    assert real.status_code == unknown.status_code == garbage.status_code == 401
    assert real.text == unknown.text == garbage.text


def test_paired_device_sees_picker_and_can_log_in_with_pin(unpaired_client, db_session):
    parent = _register(unpaired_client, "a@example.ch")
    family_id = _user(db_session, "a@example.ch").family_id
    child = _child(db_session, family_id, "Anna")
    dashboard = _child(db_session, family_id, "Wand", pin="9999", role=UserRole.DASHBOARD)
    header = {"X-Device-Token": _pair(unpaired_client, parent)["device_token"]}

    accounts = unpaired_client.get(f"/families/{family_id}/pin-accounts", headers=header)
    assert accounts.status_code == 200
    assert {a["id"] for a in accounts.json()} == {str(child.id), str(dashboard.id)}

    children = unpaired_client.get(f"/families/{family_id}/child-accounts", headers=header)
    assert children.status_code == 200
    assert [c["id"] for c in children.json()] == [str(child.id)]

    ok = unpaired_client.post(
        "/auth/pin", headers=header, json={"family_id": str(family_id), "user_id": str(child.id), "pin": "1234"}
    )
    assert ok.status_code == 200 and ok.json()["access_token"]
    wrong = unpaired_client.post(
        "/auth/pin", headers=header, json={"family_id": str(family_id), "user_id": str(child.id), "pin": "0000"}
    )
    assert wrong.status_code == 401


def test_child_accounts_via_login_child_sees_only_itself_parent_and_dashboard_see_all(unpaired_client, db_session):
    parent = _register(unpaired_client, "a@example.ch")
    family_id = _user(db_session, "a@example.ch").family_id
    anna = _child(db_session, family_id, "Anna")
    ben = _child(db_session, family_id, "Ben")
    dashboard = _child(db_session, family_id, "Wand", pin="9999", role=UserRole.DASHBOARD)
    header = {"X-Device-Token": _pair(unpaired_client, parent)["device_token"]}

    def login(user, pin):
        response = unpaired_client.post(
            "/auth/pin", headers=header, json={"family_id": str(family_id), "user_id": str(user.id), "pin": pin}
        )
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    url = f"/families/{family_id}/child-accounts"
    assert {c["id"] for c in unpaired_client.get(url, headers=parent).json()} == {str(anna.id), str(ben.id)}
    assert {c["id"] for c in unpaired_client.get(url, headers=login(dashboard, "9999")).json()} == {
        str(anna.id),
        str(ben.id),
    }
    assert [c["id"] for c in unpaired_client.get(url, headers=login(anna, "1234")).json()] == [str(anna.id)]


def test_child_accounts_login_of_another_family_is_rejected(unpaired_client, db_session):
    _register(unpaired_client, "a@example.ch", "A")
    parent_b = _register(unpaired_client, "b@example.ch", "B")
    family_a = _user(db_session, "a@example.ch").family_id
    _child(db_session, family_a)
    assert unpaired_client.get(f"/families/{family_a}/child-accounts", headers=parent_b).status_code == 401


def test_no_unauthenticated_endpoint_exposes_children(unpaired_client):
    """Routes needing neither a login nor a device token must be exactly the known harmless ones."""
    import importlib
    import pkgutil

    from fastapi.routing import APIRoute

    import app.api as api_package
    from app.api import deps

    guards = {deps.get_current_user, deps.require_parent, deps.require_device, deps.bearer_scheme}

    def dependency_calls(dependant):
        for sub in dependant.dependencies:
            yield sub.call
            yield from dependency_calls(sub)

    open_routes = set()
    for module_info in pkgutil.iter_modules(api_package.__path__):
        router = getattr(importlib.import_module(f"app.api.{module_info.name}"), "router", None)
        for route in getattr(router, "routes", []):
            if isinstance(route, APIRoute) and not (set(dependency_calls(route.dependant)) & guards):
                open_routes.add((sorted(route.methods)[0], route.path))

    assert open_routes == {
        ("POST", "/auth/register-family"),
        ("POST", "/auth/login"),
        ("POST", "/auth/password-reset/request"),
        ("POST", "/auth/password-reset/confirm"),
        ("GET", "/calendar/sync/google/callback"),  # OAuth redirect, protected by a signed state
        ("GET", "/notifications/vapid-public-key"),  # public key, no family data
    }
