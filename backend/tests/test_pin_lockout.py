"""PIN brute-force protection: per-user lockout with escalation, per-device throttle, parent recovery."""

import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from app.api.deps import hash_device_token
from app.core import pin_lockout
from app.core.security import hash_secret
from app.models.family import Family
from app.models.family_device import FamilyDevice
from app.models.user import User, UserRole

PIN = "1234"
WRONG = "0000"


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


@pytest.fixture()
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(pin_lockout, "clock", fake)
    return fake


def _register(client, email="a@example.ch", family_name="Muster"):
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


def _family_id(db, email="a@example.ch"):
    return db.scalar(select(User).where(User.email == email)).family_id


def _user(db, family_id, name="Anna", pin=PIN, role=UserRole.CHILD):
    user = User(family_id=family_id, role=role, display_name=name, pin_hash=hash_secret(pin))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _device_header(client, parent, label="Tablet"):
    response = client.post("/families/devices", headers=parent, json={"label": label})
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["device_token"]}


def _login(client, header, family_id, user_id, pin, path="/auth/pin"):
    return client.post(path, headers=header, json={"family_id": str(family_id), "user_id": str(user_id), "pin": pin})


@pytest.fixture()
def setup(unpaired_client, db_session, clock):
    parent = _register(unpaired_client)
    family_id = _family_id(db_session)
    anna = _user(db_session, family_id)
    header = _device_header(unpaired_client, parent)
    return unpaired_client, db_session, parent, family_id, anna, header


def _fail(client, header, family_id, user, n, pin=WRONG):
    return [_login(client, header, family_id, user.id, pin) for _ in range(n)]


def _refresh(db, obj):
    db.refresh(obj)
    return obj


def test_five_wrong_pins_lock_the_user_with_429_and_retry_after(setup, clock):
    client, db, _, fid, anna, header = setup
    responses = _fail(client, header, fid, anna, 4)
    assert [r.status_code for r in responses] == [401] * 4
    assert all(r.json()["detail"] == "PIN falsch" for r in responses)

    fifth = _login(client, header, fid, anna.id, WRONG)
    assert fifth.status_code == 429
    assert fifth.headers["Retry-After"] == "300"
    assert fifth.json()["detail"] == "Zu viele Versuche. Bitte in 5 Minuten erneut versuchen."
    assert _refresh(db, anna).pin_locked_until == clock.now + timedelta(minutes=5)
    assert anna.pin_lock_level == 1


def test_correct_pin_is_refused_while_locked_and_not_verified(setup, monkeypatch):
    client, _, _, fid, anna, header = setup
    _fail(client, header, fid, anna, 5)
    calls = []
    monkeypatch.setattr(pin_lockout, "verify_secret", lambda *a: calls.append(a) or True)

    for path in ("/auth/pin", "/auth/child-pin"):
        response = _login(client, header, fid, anna.id, PIN, path)
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0
    assert calls == []


def test_retry_after_counts_down_and_message_uses_minutes(setup, clock):
    client, _, _, fid, anna, header = setup
    _fail(client, header, fid, anna, 5)
    clock.advance(minutes=3, seconds=30)
    response = _login(client, header, fid, anna.id, PIN)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "90"
    assert response.json()["detail"] == "Zu viele Versuche. Bitte in 2 Minuten erneut versuchen."
    clock.advance(seconds=60)
    response = _login(client, header, fid, anna.id, PIN)
    assert response.headers["Retry-After"] == "30"
    assert response.json()["detail"] == "Zu viele Versuche. Bitte in 1 Minute erneut versuchen."


def test_lock_expires_by_time_and_correct_pin_then_works(setup, clock):
    client, db, _, fid, anna, header = setup
    _fail(client, header, fid, anna, 5)
    clock.advance(minutes=5, seconds=1)
    ok = _login(client, header, fid, anna.id, PIN)
    assert ok.status_code == 200 and ok.json()["access_token"]
    _refresh(db, anna)
    assert (anna.failed_pin_attempts, anna.pin_locked_until, anna.pin_lock_level) == (0, None, 0)


def test_lock_levels_escalate_5_15_60_and_cap_at_60(setup, clock):
    client, db, _, fid, anna, header = setup
    seen = []
    for _ in range(5):
        clock.advance(hours=2)  # far beyond the device window so only the user lock matters
        last = _fail(client, header, fid, anna, 5)[-1]
        assert last.status_code == 429
        seen.append(int(last.headers["Retry-After"]) // 60)
        clock.advance(minutes=61)
    assert seen == [5, 15, 60, 60, 60]
    assert _refresh(db, anna).pin_lock_level == 5  # counts on, the duration is capped


def test_attempts_restart_after_a_lock_expired(setup, clock):
    client, _, _, fid, anna, header = setup
    _fail(client, header, fid, anna, 5)
    clock.advance(minutes=6)
    responses = _fail(client, header, fid, anna, 4)
    assert [r.status_code for r in responses] == [401] * 4
    fifth = _login(client, header, fid, anna.id, WRONG)
    assert fifth.status_code == 429 and fifth.headers["Retry-After"] == "900"  # level 2


def test_successful_login_resets_attempts_and_level(setup, clock):
    client, db, _, fid, anna, header = setup
    _fail(client, header, fid, anna, 5)
    clock.advance(minutes=6)  # level 1 lock expired
    _fail(client, header, fid, anna, 3)
    assert _refresh(db, anna).failed_pin_attempts == 3 and anna.pin_lock_level == 1
    assert _login(client, header, fid, anna.id, PIN).status_code == 200
    assert (_refresh(db, anna).failed_pin_attempts, anna.pin_lock_level) == (0, 0)
    # a fresh series needs five wrong PINs again and starts at level 1 (5 minutes)
    responses = _fail(client, header, fid, anna, 5)
    assert [r.status_code for r in responses] == [401] * 4 + [429]
    assert responses[-1].headers["Retry-After"] == "300"


def test_lock_is_per_user(setup):
    client, db, _, fid, anna, header = setup
    ben = _user(db, fid, "Ben", pin="5678")
    _fail(client, header, fid, anna, 5)
    assert _login(client, header, fid, ben.id, "5678").status_code == 200


def test_dashboard_user_is_locked_too(setup):
    client, db, _, fid, _, header = setup
    wall = _user(db, fid, "Wand", pin="9999", role=UserRole.DASHBOARD)
    assert _fail(client, header, fid, wall, 5)[-1].status_code == 429
    assert _login(client, header, fid, wall.id, "9999").status_code == 429


# --- per-device throttle ---


def _many_children(db, fid, n):
    return [_user(db, fid, f"Kind{i}", pin="5678") for i in range(n)]


def test_device_is_throttled_after_20_failures_across_users(setup, clock):
    client, db, _, fid, anna, header = setup
    kids = _many_children(db, fid, 10)  # 2 failures each: nobody reaches the per-user limit
    for kid in kids:
        assert [r.status_code for r in _fail(client, header, fid, kid, 2)] == [401, 401]
    response = _login(client, header, fid, anna.id, PIN)  # 20 failures used up: even a correct PIN is refused
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3600"
    assert "Zu viele Versuche" in response.json()["detail"]
    clock.advance(minutes=59)
    assert _login(client, header, fid, anna.id, PIN).headers["Retry-After"] == "60"
    clock.advance(minutes=1, seconds=1)
    assert _login(client, header, fid, anna.id, PIN).status_code == 200  # window over


def test_device_counter_is_not_reset_by_success(setup):
    client, db, _, fid, anna, header = setup
    ben = _user(db, fid, "Ben", pin="5678")
    for _ in range(4):
        _fail(client, header, fid, ben, 4)
        assert _login(client, header, fid, ben.id, "5678").status_code == 200
    device = db.scalar(select(FamilyDevice))
    assert device.failed_pin_attempts == 16
    _fail(client, header, fid, anna, 3)
    assert _login(client, header, fid, anna.id, PIN).status_code == 200  # 19 used: success is still allowed
    assert _refresh(db, device).failed_pin_attempts == 19  # ...and does not reduce the count
    assert _login(client, header, fid, ben.id, WRONG).status_code == 401  # the 20th failure
    assert _login(client, header, fid, ben.id, "5678").status_code == 429  # now the device is blocked


def test_device_throttle_is_per_device(setup):
    client, db, parent, fid, anna, header = setup
    other = _device_header(client, parent, "Zweites Tablet")
    kids = _many_children(db, fid, 10)
    for kid in kids:
        _fail(client, header, fid, kid, 2)
    assert _login(client, header, fid, anna.id, PIN).status_code == 429
    assert _login(client, other, fid, anna.id, PIN).status_code == 200


def test_unknown_user_counts_only_against_the_device(setup):
    client, db, _, fid, anna, header = setup
    responses = [_login(client, header, fid, uuid.uuid4(), WRONG) for _ in range(20)]
    assert {r.status_code for r in responses} == {401}
    assert {r.text for r in responses} == {'{"detail":"PIN falsch"}'}
    assert db.scalar(select(FamilyDevice)).failed_pin_attempts == 20
    assert _refresh(db, anna).failed_pin_attempts == 0
    assert _login(client, header, fid, uuid.uuid4(), WRONG).status_code == 429


# --- no information leaks ---


def test_without_valid_device_everything_is_the_same_401_even_for_locked_users(setup):
    client, db, parent, fid, anna, header = setup
    _fail(client, header, fid, anna, 5)  # anna is locked now
    other_parent = _register(client, "b@example.ch", "Andere")
    other_header = _device_header(client, other_parent)
    revoked = _device_header(client, parent, "Alt")
    revoked_hash = hash_device_token(revoked["X-Device-Token"])
    device = db.scalar(select(FamilyDevice).where(FamilyDevice.token_hash == revoked_hash))
    device.revoked_at = datetime.now(UTC)
    db.commit()

    bad_headers = [{}, {"X-Device-Token": "garbage"}, other_header, revoked]
    responses = [_login(client, h, fid, anna.id, PIN) for h in bad_headers]
    assert {r.status_code for r in responses} == {401}
    assert len({r.text for r in responses}) == 1
    assert all("Retry-After" not in r.headers for r in responses)
    # a valid device of family B cannot even ask about family A's (locked) user: same 401 as any device failure
    foreign = _login(client, other_header, fid, anna.id, PIN)
    assert foreign.status_code == 401 and foreign.text == responses[0].text


def test_wrong_family_or_unknown_user_are_plain_401_never_429(setup):
    client, db, _, fid, anna, header = setup
    other_family = Family(name="Fremd")
    db.add(other_family)
    db.commit()
    stranger = _user(db, other_family.id, "Fremdkind")
    _fail(client, header, other_family.id, stranger, 1)
    r_wrong_family = _login(client, header, other_family.id, stranger.id, PIN)  # device belongs to family A
    r_cross = _login(client, header, fid, stranger.id, PIN)
    r_unknown = _login(client, header, fid, uuid.uuid4(), PIN)
    assert {r.status_code for r in (r_wrong_family, r_cross, r_unknown)} == {401}
    assert _refresh(db, stranger).failed_pin_attempts == 0  # foreign users are never touched
    assert _fail(client, header, fid, stranger, 6)[-1].status_code == 401  # never locked via this family


# --- atomicity ---


def test_counter_update_takes_row_locks(setup):
    client, db, _, fid, anna, header = setup
    statements = []

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    engine = db.get_bind().engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        _login(client, header, fid, anna.id, WRONG)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    locking = [s for s in statements if "FOR UPDATE" in s]
    assert any('FROM "user"' in s or "FROM user" in s for s in locking)
    assert any("family_device" in s for s in locking)


def test_parallel_wrong_guesses_cannot_exceed_the_limit(_engine):
    """Real concurrent transactions from two devices against the same user: exactly 4 plain wrong-PIN answers,
    the 5th locks, everything else is refused."""
    with Session(_engine) as s:
        family = Family(name="Parallel")
        s.add(family)
        s.flush()
        user = User(family_id=family.id, role=UserRole.CHILD, display_name="P", pin_hash=hash_secret(PIN))
        devices = [
            FamilyDevice(family_id=family.id, label=f"d{i}", token_hash=hash_device_token(secrets.token_urlsafe(16)))
            for i in range(2)
        ]
        s.add(user)
        s.add_all(devices)
        s.commit()
        family_id, user_id, device_ids = family.id, user.id, [d.id for d in devices]

    codes = []
    lock = threading.Lock()

    def attempt(device_id):
        with Session(_engine) as session:
            device = session.get(FamilyDevice, device_id)
            try:
                pin_lockout.pin_login_user(session, device, family_id, user_id, WRONG)
                code = 200
            except HTTPException as exc:
                code = exc.status_code
        with lock:
            codes.append(code)

    try:
        threads = [threading.Thread(target=attempt, args=(device_ids[i % 2],)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with Session(_engine) as s:
            stored = s.get(User, user_id)
            assert (stored.failed_pin_attempts, stored.pin_lock_level) == (0, 1)
        assert sorted(codes) == [401] * 4 + [429] * 12
    finally:
        with Session(_engine) as s:
            s.execute(delete(Family).where(Family.id == family_id))
            s.commit()


# --- parent recovery ---


def test_parent_changing_child_pin_resets_lock(setup):
    client, db, parent, fid, anna, header = setup
    _fail(client, header, fid, anna, 5)
    assert _login(client, header, fid, anna.id, PIN).status_code == 429
    r = client.patch(f"/families/children/{anna.id}", headers=parent, json={"pin": "4321"})
    assert r.status_code == 200
    assert (_refresh(db, anna).failed_pin_attempts, anna.pin_locked_until, anna.pin_lock_level) == (0, None, 0)
    assert _login(client, header, fid, anna.id, "4321").status_code == 200


def test_patch_without_pin_keeps_the_lock(setup):
    client, db, parent, fid, anna, header = setup
    _fail(client, header, fid, anna, 5)
    client.patch(f"/families/children/{anna.id}", headers=parent, json={"display_name": "Anni"})
    assert _refresh(db, anna).pin_locked_until is not None


def test_parent_changing_dashboard_pin_resets_lock(setup):
    client, db, parent, fid, _, header = setup
    wall = _user(db, fid, "Wand", pin="9999", role=UserRole.DASHBOARD)
    _fail(client, header, fid, wall, 5)
    r = client.put("/families/dashboard-user", headers=parent, json={"pin": "1111"})
    assert r.status_code == 200
    assert _refresh(db, wall).pin_locked_until is None and wall.pin_lock_level == 0
    assert _login(client, header, fid, wall.id, "1111").status_code == 200


def test_parent_unlock_endpoint_clears_lock_for_child_and_dashboard(setup):
    client, db, parent, fid, anna, header = setup
    wall = _user(db, fid, "Wand", pin="9999", role=UserRole.DASHBOARD)
    for user, pin in ((anna, PIN), (wall, "9999")):
        _fail(client, header, fid, user, 5)
        assert client.post(f"/families/children/{user.id}/unlock", headers=parent).status_code == 204
        assert (_refresh(db, user).pin_locked_until, user.pin_lock_level, user.failed_pin_attempts) == (None, 0, 0)
        assert _login(client, header, fid, user.id, pin).status_code == 200


def test_unlock_is_parent_only_and_tenancy_checked(setup):
    client, db, parent, fid, anna, header = setup
    _fail(client, header, fid, anna, 5)
    other_parent = _register(client, "b@example.ch", "Andere")
    other_family = _family_id(db, "b@example.ch")
    foreign_child = _user(db, other_family, "Fremd")
    assert client.post(f"/families/children/{anna.id}/unlock", headers=other_parent).status_code == 404
    assert client.post(f"/families/children/{uuid.uuid4()}/unlock", headers=parent).status_code == 404
    assert client.post(f"/families/children/{foreign_child.id}/unlock", headers=parent).status_code == 404
    assert client.post(f"/families/children/{anna.id}/unlock").status_code == 401
    parent_id = db.scalar(select(User).where(User.email == "a@example.ch")).id
    assert client.post(f"/families/children/{parent_id}/unlock", headers=parent).status_code == 404  # parents no
    assert _refresh(db, anna).pin_locked_until is not None
    child_token = _login(client, header, fid, _user(db, fid, "Ben", pin="5678").id, "5678").json()["access_token"]
    child_headers = {"Authorization": f"Bearer {child_token}"}
    assert client.post(f"/families/children/{anna.id}/unlock", headers=child_headers).status_code == 403
    assert _refresh(db, anna).pin_locked_until is not None


# --- lock info in the child list ---


def test_only_parents_see_lock_state_in_the_child_list(setup, clock):
    client, db, parent, fid, anna, header = setup
    ben = _user(db, fid, "Ben", pin="5678")
    wall = _user(db, fid, "Wand", pin="9999", role=UserRole.DASHBOARD)
    _fail(client, header, fid, anna, 5)

    listing = client.get("/families/children", headers=parent).json()
    by_name = {c["display_name"]: c for c in listing}
    assert by_name["Anna"]["pin_locked_until"] is not None
    assert by_name["Ben"]["pin_locked_until"] is None

    ben_headers = {"Authorization": f"Bearer {_login(client, header, fid, ben.id, '5678').json()['access_token']}"}
    own = client.get("/families/children", headers=ben_headers)
    assert [c["display_name"] for c in own.json()] == ["Ben"]
    assert "Anna" not in own.text

    wall_headers = {"Authorization": f"Bearer {_login(client, header, fid, wall.id, '9999').json()['access_token']}"}
    wall_list = client.get("/families/children", headers=wall_headers).json()
    assert {c["display_name"] for c in wall_list} == {"Anna", "Ben"}
    assert all(c["pin_locked_until"] is None for c in wall_list)

    clock.advance(minutes=6)  # expired locks are not reported
    assert all(c["pin_locked_until"] is None for c in client.get("/families/children", headers=parent).json())
