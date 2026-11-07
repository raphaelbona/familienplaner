"""Preference API: access rules, tenancy and validation."""

from app.models.user import UserRole
from tests.food_helpers import add_member, admin_user, pin_headers, register_family


def _setup(client, db, email):
    headers, fam = register_family(client, email)
    parent = admin_user(db, fam)
    child1 = add_member(db, fam, UserRole.CHILD, "Anna", 2016)
    child2 = add_member(db, fam, UserRole.CHILD, "Bob", 2018)
    return headers, fam, parent, child1, child2


def test_child_cannot_read_or_write_other_members_prefs(client, db_session):
    _, fam, parent, c1, c2 = _setup(client, db_session, "pref-a@example.ch")
    ch = pin_headers(client, fam, c1)
    assert client.get(f"/food-preferences/members/{c2.id}", headers=ch).status_code == 403
    assert client.get(f"/food-preferences/members/{parent.id}", headers=ch).status_code == 403
    body = {"likes": ["x"], "dislikes": [], "allergies": []}
    assert client.put(f"/food-preferences/members/{c2.id}", headers=ch, json=body).status_code == 403
    assert client.put(f"/food-preferences/members/{parent.id}", headers=ch, json=body).status_code == 403


def test_parent_can_manage_any_family_member_incl_other_adult(client, db_session):
    headers, fam, parent, c1, _ = _setup(client, db_session, "pref-b@example.ch")
    other_adult = add_member(db_session, fam, UserRole.PARENT_MEMBER, "Papa", 1985)
    for target in (c1, other_adult, parent):
        body = {"likes": ["Pasta"], "dislikes": ["Pilze"], "allergies": ["Nuss"]}
        assert client.put(f"/food-preferences/members/{target.id}", headers=headers, json=body).status_code == 200
        got = client.get(f"/food-preferences/members/{target.id}", headers=headers).json()
        assert got == {"likes": ["Pasta"], "dislikes": ["Pilze"], "allergies": ["Nuss"]}


def test_dashboard_role_is_forbidden_read_and_write(client, db_session):
    _, fam, _, c1, _ = _setup(client, db_session, "pref-c@example.ch")
    dash = add_member(db_session, fam, UserRole.DASHBOARD, "Wand", None, pin="2468")
    dh = pin_headers(client, fam, dash, pin="2468")
    assert client.get(f"/food-preferences/members/{c1.id}", headers=dh).status_code == 403
    assert client.get(f"/food-preferences/members/{dash.id}", headers=dh).status_code == 403
    body = {"likes": [], "dislikes": [], "allergies": []}
    assert client.put(f"/food-preferences/members/{c1.id}", headers=dh, json=body).status_code == 403
    assert client.get("/food-preferences", headers=dh).status_code == 403


def test_cross_family_member_is_404(client, db_session):
    headers, _, _, _, _ = _setup(client, db_session, "pref-d@example.ch")
    _, fam_b, _, other_child, _ = _setup(client, db_session, "pref-e@example.ch")
    assert client.get(f"/food-preferences/members/{other_child.id}", headers=headers).status_code == 404
    body = {"likes": ["x"], "dislikes": [], "allergies": []}
    assert client.put(f"/food-preferences/members/{other_child.id}", headers=headers, json=body).status_code == 404


def test_invalid_member_id_is_422(client, db_session):
    headers, *_ = _setup(client, db_session, "pref-f@example.ch")
    assert client.get("/food-preferences/members/nope", headers=headers).status_code == 422


def test_put_replaces_whole_set(client, db_session):
    headers, _, _, c1, _ = _setup(client, db_session, "pref-g@example.ch")
    url = f"/food-preferences/members/{c1.id}"
    client.put(url, headers=headers, json={"likes": ["A", "B"], "dislikes": ["C"], "allergies": ["D"]})
    client.put(url, headers=headers, json={"likes": ["Z"], "dislikes": [], "allergies": []})
    assert client.get(url, headers=headers).json() == {"likes": ["Z"], "dislikes": [], "allergies": []}


def test_list_caps_are_enforced(client, db_session):
    headers, _, _, c1, _ = _setup(client, db_session, "pref-h@example.ch")
    url = f"/food-preferences/members/{c1.id}"
    ok = {"likes": [f"item{i}" for i in range(50)], "dislikes": [], "allergies": []}
    assert client.put(url, headers=headers, json=ok).status_code == 200
    too_many = {"likes": [f"item{i}" for i in range(51)], "dislikes": [], "allergies": []}
    assert client.put(url, headers=headers, json=too_many).status_code == 422
    too_long = {"likes": [], "dislikes": ["x" * 81], "allergies": []}
    assert client.put(url, headers=headers, json=too_long).status_code == 422
    exactly = {"likes": [], "dislikes": ["x" * 80], "allergies": []}
    assert client.put(url, headers=headers, json=exactly).status_code == 200
    # nothing was changed by the rejected requests
    assert client.get(url, headers=headers).json()["dislikes"] == ["x" * 80]


def test_blank_entries_are_dropped(client, db_session):
    headers, _, _, c1, _ = _setup(client, db_session, "pref-i@example.ch")
    url = f"/food-preferences/members/{c1.id}"
    client.put(url, headers=headers, json={"likes": ["  ", "Reis"], "dislikes": [], "allergies": []})
    assert client.get(url, headers=headers).json()["likes"] == ["Reis"]


def test_family_overview_lists_all_three_kinds(client, db_session):
    headers, _, _, c1, _ = _setup(client, db_session, "pref-j@example.ch")
    client.put(
        f"/food-preferences/members/{c1.id}",
        headers=headers,
        json={"likes": ["A"], "dislikes": ["B"], "allergies": ["C"]},
    )
    overview = {m["user_id"]: m for m in client.get("/food-preferences", headers=headers).json()}
    entry = overview[str(c1.id)]
    assert (entry["likes"], entry["dislikes"], entry["allergies"]) == (["A"], ["B"], ["C"])
