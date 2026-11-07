"""A child login must not read siblings' data; the parent document archive is parents-only."""

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.core.security import hash_secret
from app.models.document import ParentDocument
from app.models.points import EarningsLedgerEntry
from app.models.user import User, UserRole


def _make_user(db, family_id, name, role, pin):
    user = User(family_id=family_id, role=role, display_name=name, pin_hash=hash_secret(pin))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def family(client, db_session):
    response = client.post(
        "/auth/register-family",
        json={
            "family_name": "Muster",
            "admin_display_name": "Papa",
            "admin_email": "eltern@example.ch",
            "admin_password": "s3hr-sicher",
        },
    )
    parent = {"Authorization": f"Bearer {response.json()['access_token']}"}
    admin = db_session.scalar(select(User).where(User.email == "eltern@example.ch"))
    fid = admin.family_id
    anna = _make_user(db_session, fid, "Anna", UserRole.CHILD, "1111")
    ben = _make_user(db_session, fid, "Ben", UserRole.CHILD, "2222")
    wall = _make_user(db_session, fid, "Wand", UserRole.DASHBOARD, "9999")

    def login(user, pin):
        r = client.post("/auth/pin", json={"family_id": str(fid), "user_id": str(user.id), "pin": pin})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return {
        "fid": fid,
        "anna": anna,
        "ben": ben,
        "parent": parent,
        "h": {"parent": parent, "anna": login(anna, "1111"), "ben": login(ben, "2222"), "wall": login(wall, "9999")},
    }


# ------------------------------------------------------------------ earnings


def test_child_reads_own_earnings_balance_and_ledger(client, family):
    for path in ("/earnings/balance", "/earnings/ledger"):
        assert client.get(path, headers=family["h"]["anna"]).status_code == 200
        own = client.get(path, headers=family["h"]["anna"], params={"user_id": str(family["anna"].id)})
        assert own.status_code == 200


def test_child_cannot_read_sibling_earnings(client, family):
    for path in ("/earnings/balance", "/earnings/ledger"):
        response = client.get(path, headers=family["h"]["anna"], params={"user_id": str(family["ben"].id)})
        assert response.status_code == 403, path


def test_child_cannot_read_parent_earnings(client, db_session, family):
    admin = db_session.scalar(select(User).where(User.email == "eltern@example.ch"))
    response = client.get("/earnings/balance", headers=family["h"]["anna"], params={"user_id": str(admin.id)})
    assert response.status_code == 403


def test_parent_and_dashboard_read_any_child_earnings(client, family):
    for who in ("parent", "wall"):
        for path in ("/earnings/balance", "/earnings/ledger"):
            response = client.get(path, headers=family["h"][who], params={"user_id": str(family["ben"].id)})
            assert response.status_code == 200, (who, path)


def test_earnings_sibling_data_is_not_returned_even_with_entries(client, db_session, family):
    db_session.add(EarningsLedgerEntry(user_id=family["ben"].id, amount_chf=5, status="approved"))
    db_session.commit()
    assert client.get("/earnings/ledger", headers=family["h"]["anna"]).json() == []
    ben_view = client.get("/earnings/ledger", headers=family["h"]["ben"]).json()
    assert len(ben_view) == 1


# ------------------------------------------------------------------ tasks


@pytest.fixture()
def instances(client, family):
    template = client.post(
        "/tasks/templates",
        headers=family["parent"],
        json={"title": "Zimmer aufräumen", "kind": "basis", "points_value": 0},
    ).json()
    ids = {}
    for name in ("anna", "ben"):
        response = client.post(
            "/tasks/instances",
            headers=family["parent"],
            json={
                "template_id": template["id"],
                "assignee_user_id": str(family[name].id),
                "due_date": date.today().isoformat(),
            },
        )
        assert response.status_code == 201, response.text
        ids[name] = response.json()["id"]
    return ids


def test_child_sees_only_own_task_instances(client, family, instances):
    rows = client.get("/tasks/instances", headers=family["h"]["anna"]).json()
    assert [r["id"] for r in rows] == [instances["anna"]]


def test_child_asking_for_sibling_instances_gets_nothing(client, family, instances):
    rows = client.get(
        "/tasks/instances", headers=family["h"]["anna"], params={"assignee_user_id": str(family["ben"].id)}
    ).json()
    assert rows == []


def test_child_own_instances_with_explicit_own_filter_and_status(client, family, instances):
    rows = client.get(
        "/tasks/instances",
        headers=family["h"]["anna"],
        params={"assignee_user_id": str(family["anna"].id), "status_filter": "open"},
    ).json()
    assert [r["id"] for r in rows] == [instances["anna"]]


def test_child_can_still_complete_own_task(client, family, instances):
    response = client.post(f"/tasks/instances/{instances['anna']}/complete", headers=family["h"]["anna"])
    assert response.status_code == 200


def test_parent_and_dashboard_see_all_instances(client, family, instances):
    for who in ("parent", "wall"):
        rows = client.get("/tasks/instances", headers=family["h"][who]).json()
        assert {r["id"] for r in rows} == set(instances.values()), who


# ------------------------------------------------------------------ documents


@pytest.fixture()
def document(db_session, family):
    admin = db_session.scalar(select(User).where(User.email == "eltern@example.ch"))
    doc = ParentDocument(
        family_id=family["fid"],
        uploaded_by=admin.id,
        original_filename="brief.png",
        stored_path="does/not/matter.png",
        content_type="image/png",
        status="processed",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def test_documents_are_parent_only(client, family, document):
    assert client.get("/documents", headers=family["parent"]).status_code == 200
    for who in ("anna", "wall"):
        assert client.get("/documents", headers=family["h"][who]).status_code == 403, who
        file_response = client.get(f"/documents/{document.id}/file", headers=family["h"][who])
        assert file_response.status_code == 403, who
        upload = client.post(
            "/documents/upload", headers=family["h"][who], files={"file": ("a.png", b"x", "image/png")}
        )
        assert upload.status_code == 403, who
        resolve = client.post(
            f"/documents/{document.id}/events/{uuid.uuid4()}/resolve", headers=family["h"][who], json={}
        )
        assert resolve.status_code == 403, who
    assert client.get("/documents").status_code == 401
