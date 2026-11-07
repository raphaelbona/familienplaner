"""Stable per-child color index: unit tests of the helper and of the endpoints that expose it."""

from sqlalchemy import select

from app.core.person_color import child_color_indices
from app.core.security import hash_secret
from app.models.family import Family
from app.models.user import User, UserRole


def _family(db_session, name="Muster"):
    family = Family(name=name)
    db_session.add(family)
    db_session.commit()
    return family


def _user(db_session, family, display_name, role=UserRole.CHILD, pin="1234"):
    user = User(family_id=family.id, role=role, display_name=display_name, pin_hash=hash_secret(pin))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


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
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _pin_headers(client, user, pin):
    response = client.post(
        "/auth/pin", json={"family_id": str(user.family_id), "user_id": str(user.id), "pin": pin}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --- helper ---------------------------------------------------------------------------------------------


def test_indices_follow_display_name_order(db_session):
    family = _family(db_session)
    noah = _user(db_session, family, "Noah")
    anna = _user(db_session, family, "Anna")
    mia = _user(db_session, family, "Mia")

    assert child_color_indices(db_session, family.id) == {anna.id: 0, mia.id: 1, noah.id: 2}


def test_equal_names_are_ordered_by_id(db_session):
    family = _family(db_session)
    first = _user(db_session, family, "Noah")
    second = _user(db_session, family, "Noah")

    indices = child_color_indices(db_session, family.id)

    assert sorted(indices.values()) == [0, 1]
    lower, higher = sorted([first, second], key=lambda u: u.id)
    assert indices[lower.id] == 0
    assert indices[higher.id] == 1


def test_only_children_get_an_index(db_session):
    family = _family(db_session)
    _user(db_session, family, "Anna", role=UserRole.PARENT_ADMIN)
    wall = _user(db_session, family, "Aaa-Wand", role=UserRole.DASHBOARD)
    bob = _user(db_session, family, "Bob")

    indices = child_color_indices(db_session, family.id)

    assert indices == {bob.id: 0}
    assert wall.id not in indices


def test_other_families_do_not_influence_indices(db_session):
    family = _family(db_session)
    other = _family(db_session, name="Fremd")
    _user(db_session, other, "Aaron")
    _user(db_session, other, "Abel")
    mia = _user(db_session, family, "Mia")

    assert child_color_indices(db_session, family.id) == {mia.id: 0}


def test_empty_family_has_no_indices(db_session):
    assert child_color_indices(db_session, _family(db_session).id) == {}


def test_new_child_with_earlier_name_shifts_later_indices(db_session):
    """Known limitation (documented in app/core/person_color.py): the index is derived from the name order."""
    family = _family(db_session)
    mia = _user(db_session, family, "Mia")
    assert child_color_indices(db_session, family.id)[mia.id] == 0

    anna = _user(db_session, family, "Anna")

    assert child_color_indices(db_session, family.id) == {anna.id: 0, mia.id: 1}


# --- endpoints ------------------------------------------------------------------------------------------


def _family_with_three_children(client, db_session):
    parent_headers = _register_parent(client)
    admin = db_session.scalar(select(User).where(User.email == "eltern@example.ch"))
    family = db_session.get(Family, admin.family_id)
    noah = _user(db_session, family, "Noah", pin="3333")
    anna = _user(db_session, family, "Anna", pin="1111")
    mia = _user(db_session, family, "Mia", pin="2222")
    wall = _user(db_session, family, "Wand", role=UserRole.DASHBOARD, pin="9999")
    return parent_headers, family, {"anna": anna, "mia": mia, "noah": noah}, wall


def test_children_list_has_the_same_color_index_for_parent_and_dashboard(client, db_session):
    parent_headers, _family_row, kids, wall = _family_with_three_children(client, db_session)

    as_parent = client.get("/families/children", headers=parent_headers).json()
    as_wall = client.get("/families/children", headers=_pin_headers(client, wall, "9999")).json()

    expected = {str(kids["anna"].id): 0, str(kids["mia"].id): 1, str(kids["noah"].id): 2}
    assert {c["id"]: c["color_index"] for c in as_parent} == expected
    assert {c["id"]: c["color_index"] for c in as_wall} == expected
    # listed in color-index order
    assert [c["color_index"] for c in as_parent] == [0, 1, 2]


def test_children_list_for_a_child_has_only_itself_with_the_family_wide_index(client, db_session):
    _parent, _family_row, kids, _wall = _family_with_three_children(client, db_session)

    response = client.get("/families/children", headers=_pin_headers(client, kids["noah"], "3333"))

    body = response.json()
    assert [(c["id"], c["color_index"]) for c in body] == [(str(kids["noah"].id), 2)]
    assert "Anna" not in response.text
    assert "Mia" not in response.text


def test_created_and_updated_child_report_their_color_index(client, db_session):
    parent_headers, _family_row, kids, _wall = _family_with_three_children(client, db_session)

    created = client.post(
        "/families/children", headers=parent_headers, json={"display_name": "Zoe", "pin": "4444"}
    ).json()
    updated = client.patch(
        f"/families/children/{kids['mia'].id}", headers=parent_headers, json={"avatar_key": "🦄"}
    ).json()

    assert created["color_index"] == 3
    assert updated["color_index"] == 1


def test_child_accounts_expose_color_index_for_every_caller(client, db_session):
    parent_headers, family, kids, wall = _family_with_three_children(client, db_session)
    url = f"/families/{family.id}/child-accounts"

    as_parent = client.get(url, headers=parent_headers).json()
    as_wall = client.get(url, headers=_pin_headers(client, wall, "9999")).json()
    as_device = client.get(url).json()  # auto-paired test device

    expected = {str(kids["anna"].id): 0, str(kids["mia"].id): 1, str(kids["noah"].id): 2}
    for body in (as_parent, as_wall, as_device):
        assert {c["id"]: c["color_index"] for c in body} == expected
        for account in body:
            assert set(account) == {"id", "display_name", "avatar_key", "color_index"}


def test_child_accounts_for_a_child_login_return_only_itself_with_the_family_wide_index(
    client, unpaired_client, db_session
):
    """Without a device token (plain Bearer login of a child) only the child itself comes back."""
    _parent, family, kids, _wall = _family_with_three_children(client, db_session)
    headers = _pin_headers(client, kids["noah"], "3333")

    response = unpaired_client.get(f"/families/{family.id}/child-accounts", headers=headers)

    assert response.status_code == 200
    assert [(c["id"], c["color_index"]) for c in response.json()] == [(str(kids["noah"].id), 2)]
    assert "Anna" not in response.text
    assert "Mia" not in response.text


def test_pin_accounts_picker_stays_without_color_index(client, db_session):
    _parent, family, _kids, _wall = _family_with_three_children(client, db_session)

    accounts = client.get(f"/families/{family.id}/pin-accounts").json()

    assert accounts
    for account in accounts:
        assert "color_index" not in account
