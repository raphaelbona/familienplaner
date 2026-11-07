"""API of the Google calendar sync: status / calendar selection / sync-now / disconnect, the OAuth connect and
callback (state cookie), and the server-side redaction of imported events for children and the wall dashboard."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.api import calendar as calendar_api
from app.core import google_sync
from app.core.crypto import decrypt_secret
from app.models.calendar import CalendarEvent, CalendarSyncAccount, GoogleCalendarSelection
from tests.google_helpers import (
    FakeGoogle,
    headers_for,
    http_error,
    make_account,
    make_family,
    soon,
    timed,
)

STATUS = "/calendar/sync/google/status"
CALENDARS = "/calendar/sync/google/calendars"
SYNC_NOW = "/calendar/sync/google/sync-now"
DISCONNECT = "/calendar/sync/google"
CONNECT = "/calendar/sync/google/connect"
CALLBACK = "/calendar/sync/google/callback"
ALL_ROUTES = [
    ("get", STATUS),
    ("put", CALENDARS),
    ("post", SYNC_NOW),
    ("delete", DISCONNECT),
]


def _settings(**overrides):
    values = {
        "google_client_id": "client-id",
        "google_client_secret": "client-secret",
        "google_redirect_uri": "http://localhost:8000/calendar/sync/google/callback",
        "app_base_url": "http://localhost:8090",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _clean_rate_limit():
    calendar_api._last_sync_now.clear()
    yield
    calendar_api._last_sync_now.clear()


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(calendar_api, "get_settings", lambda: _settings())


@pytest.fixture()
def fake(monkeypatch):
    """The Google API as seen by the sync code (building the client returns this fake)."""
    service = FakeGoogle(
        calendars=[
            {"id": "papa@gmail.com", "summary": "Papa", "primary": True},
            {"id": "schule@group.calendar.google.com", "summary": "Schule"},
            {"id": "sport@group.calendar.google.com", "summaryOverride": "Sport", "summary": "Verein"},
        ]
    )
    monkeypatch.setattr("app.core.google_sync.discovery.build", lambda *a, **k: service)
    return service


@pytest.fixture()
def fam(db_session):
    return make_family(db_session)


def _call(client, method, url, headers=None, **kwargs):
    return client.request(method.upper(), url, headers=headers, **kwargs)


# ------------------------------------------------------------------------------------------ access


@pytest.mark.parametrize(("method", "url"), ALL_ROUTES)
def test_unauthenticated_requests_get_401(client, method, url):
    kwargs = {"json": {"calendars": []}} if method == "put" else {}
    assert _call(client, method, url, **kwargs).status_code == 401


@pytest.mark.parametrize(("method", "url"), ALL_ROUTES)
@pytest.mark.parametrize("who", ["mia", "wall"])
def test_children_and_the_wall_dashboard_get_403(client, db_session, fam, method, url, who):
    make_account(db_session, fam["papa"])
    kwargs = {"json": {"calendars": []}} if method == "put" else {}

    assert _call(client, method, url, headers_for(fam[who]), **kwargs).status_code == 403


# ------------------------------------------------------------------------------------------- status


def test_status_not_configured_does_not_fail(client, fam, monkeypatch):
    monkeypatch.setattr(calendar_api, "get_settings", lambda: _settings(google_client_secret=None))

    body = client.get(STATUS, headers=headers_for(fam["papa"])).json()

    assert body["configured"] is False and body["connected"] is False
    assert body["calendars"] == [] and body["email"] is None and body["status"] is None


def test_status_configured_but_not_connected(client, fam, configured):
    body = client.get(STATUS, headers=headers_for(fam["papa"])).json()

    assert body == {
        "configured": True,
        "connected": False,
        "email": None,
        "status": None,
        "last_synced_at": None,
        "last_error": None,
        "calendars": [],
    }


def test_status_connected_merges_live_calendars_with_stored_selection(client, db_session, fam, configured, fake):
    account = make_account(
        db_session, fam["papa"], selections=("schule@group.calendar.google.com",),
        owners={"schule@group.calendar.google.com": [fam["mia"].id]},
    )

    body = client.get(STATUS, headers=headers_for(fam["papa"])).json()

    assert body["connected"] is True and body["email"] == "papa@gmail.com" and body["status"] == "ok"
    assert body["last_synced_at"] is not None and body["last_error"] is None
    calendars = {c["id"]: c for c in body["calendars"]}
    assert calendars["schule@group.calendar.google.com"] == {
        "id": "schule@group.calendar.google.com", "name": "Schule", "selected": True,
        "owner_user_ids": [str(fam["mia"].id)],
    }
    assert calendars["papa@gmail.com"]["selected"] is False and calendars["papa@gmail.com"]["owner_user_ids"] == []
    assert calendars["sport@group.calendar.google.com"]["name"] == "Sport"  # summaryOverride wins
    assert body["calendars"][0]["selected"] is True  # selected calendars first
    assert account.status == "ok"


def test_status_maps_a_legacy_primary_selection_to_the_primary_calendar(client, db_session, fam, configured, fake):
    make_account(db_session, fam["papa"], selections=("primary",))

    calendars = client.get(STATUS, headers=headers_for(fam["papa"])).json()["calendars"]

    selected = [c["id"] for c in calendars if c["selected"]]
    assert selected == ["papa@gmail.com"]
    assert [c["id"] for c in calendars].count("primary") == 0


def test_status_survives_a_google_failure_with_stored_selections(client, db_session, fam, configured, fake):
    make_account(db_session, fam["papa"], selections=("schule@group.calendar.google.com",))
    fake.calendar_list_error = http_error(500)

    response = client.get(STATUS, headers=headers_for(fam["papa"]))

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True and body["status"] == "error" and body["last_error"]
    assert [(c["id"], c["selected"]) for c in body["calendars"]] == [("schule@group.calendar.google.com", True)]


def test_status_marks_the_account_needs_reauth_when_google_says_unauthorized(client, db_session, fam, configured, fake):
    account = make_account(db_session, fam["papa"], selections=("primary",))
    fake.calendar_list_error = http_error(401)

    body = client.get(STATUS, headers=headers_for(fam["papa"])).json()

    assert body["status"] == "needs_reauth" and "neu verbinden" in body["last_error"].lower()
    db_session.refresh(account)
    assert account.status == "needs_reauth"


def test_status_of_a_needs_reauth_account_does_not_call_google(client, db_session, fam, configured):
    make_account(db_session, fam["papa"], status="needs_reauth", last_error="Bitte neu verbinden")

    body = client.get(STATUS, headers=headers_for(fam["papa"])).json()  # the autouse guard fails on any call

    assert body["status"] == "needs_reauth" and body["last_error"] == "Bitte neu verbinden"
    assert [c["id"] for c in body["calendars"]] == ["primary"]


def test_status_is_scoped_to_the_callers_own_account(client, db_session, fam, configured, fake):
    make_account(db_session, fam["papa"])
    other = make_family(db_session, name="Andere", tag="b")

    assert client.get(STATUS, headers=headers_for(other["papa"])).json()["connected"] is False
    assert client.get(STATUS, headers=headers_for(fam["mama"])).json()["connected"] is False  # accounts are per parent


# ------------------------------------------------------------------------------------ PUT calendars


def _selection_rows(db, account):
    db.expire_all()
    return {
        s.google_calendar_id: list(s.owner_user_ids)
        for s in db.scalars(select(GoogleCalendarSelection).where(GoogleCalendarSelection.account_id == account.id))
    }


def test_put_replaces_the_selection_and_returns_the_status(client, db_session, fam, configured, fake):
    account = make_account(db_session, fam["papa"], selections=("papa@gmail.com",))
    body = {
        "calendars": [
            {"id": "schule@group.calendar.google.com", "owner_user_ids": [str(fam["mia"].id), str(fam["leo"].id)]},
            {"id": "sport@group.calendar.google.com", "owner_user_ids": []},
        ]
    }

    response = client.put(CALENDARS, json=body, headers=headers_for(fam["papa"]))

    assert response.status_code == 200
    assert _selection_rows(db_session, account) == {
        "schule@group.calendar.google.com": [fam["mia"].id, fam["leo"].id],
        "sport@group.calendar.google.com": [],
    }
    status = response.json()
    selected = {c["id"]: c["owner_user_ids"] for c in status["calendars"] if c["selected"]}
    assert selected == {
        "schule@group.calendar.google.com": [str(fam["mia"].id), str(fam["leo"].id)],
        "sport@group.calendar.google.com": [],
    }
    names = {s.google_calendar_id: s.name for s in db_session.scalars(select(GoogleCalendarSelection))}
    assert names["sport@group.calendar.google.com"] == "Sport"  # name taken from the live list


def test_put_updates_owners_of_a_kept_calendar_and_ignores_duplicates(client, db_session, fam, configured, fake):
    account = make_account(db_session, fam["papa"], selections=("papa@gmail.com",))
    body = {"calendars": [{"id": "papa@gmail.com", "owner_user_ids": [str(fam["mama"].id), str(fam["mama"].id)]},
                          {"id": "papa@gmail.com", "owner_user_ids": []}]}

    assert client.put(CALENDARS, json=body, headers=headers_for(fam["papa"])).status_code == 200

    assert _selection_rows(db_session, account) == {"papa@gmail.com": [fam["mama"].id]}


def test_put_removes_imported_events_of_deselected_calendars_only(client, db_session, fam, configured, fake):
    account = make_account(db_session, fam["papa"], selections=("cal-a", "cal-b"))
    for cal in ("cal-a", "cal-b"):
        db_session.add(CalendarEvent(
            family_id=fam["family"].id, title=cal, start=soon(), end=soon(2), source="google",
            external_id=cal, source_calendar_id=cal, sync_account_id=account.id,
        ))
    db_session.add(CalendarEvent(family_id=fam["family"].id, title="Nativ", start=soon(), end=soon(2)))
    db_session.commit()

    response = client.put(CALENDARS, json={"calendars": [{"id": "cal-a", "owner_user_ids": []}]},
                          headers=headers_for(fam["papa"]))

    assert response.status_code == 200
    assert {e.title for e in db_session.scalars(select(CalendarEvent))} == {"cal-a", "Nativ"}


def test_put_with_an_empty_list_deselects_everything(client, db_session, fam, configured, fake):
    account = make_account(db_session, fam["papa"], selections=("cal-a",))

    response = client.put(CALENDARS, json={"calendars": []}, headers=headers_for(fam["papa"]))

    assert response.status_code == 200 and _selection_rows(db_session, account) == {}


def test_put_without_a_connected_account_is_404(client, fam, configured):
    response = client.put(CALENDARS, json={"calendars": []}, headers=headers_for(fam["papa"]))
    assert response.status_code == 404


def test_put_rejects_owners_from_another_family_and_changes_nothing(client, db_session, fam, configured, fake):
    account = make_account(db_session, fam["papa"], selections=("papa@gmail.com",), owners={"papa@gmail.com": []})
    other = make_family(db_session, name="Andere", tag="b")
    body = {"calendars": [{"id": "schule@group.calendar.google.com", "owner_user_ids": [str(other["mia"].id)]}]}

    response = client.put(CALENDARS, json=body, headers=headers_for(fam["papa"]))

    assert response.status_code == 404
    assert _selection_rows(db_session, account) == {"papa@gmail.com": []}


def test_put_rejects_unknown_owner_ids_and_mixed_lists(client, db_session, fam, configured, fake):
    account = make_account(db_session, fam["papa"], selections=("papa@gmail.com",))
    for owners in ([str(uuid.uuid4())], [str(fam["mia"].id), str(uuid.uuid4())]):
        body = {"calendars": [{"id": "papa@gmail.com", "owner_user_ids": owners}]}
        assert client.put(CALENDARS, json=body, headers=headers_for(fam["papa"])).status_code == 404
    assert client.put(CALENDARS, json={"calendars": [{"id": "x", "owner_user_ids": ["kein-uuid"]}]},
                      headers=headers_for(fam["papa"])).status_code == 422
    assert _selection_rows(db_session, account) == {"papa@gmail.com": []}


def test_family_b_cannot_modify_family_a_selection(client, db_session, fam, configured, fake):
    account = make_account(db_session, fam["papa"], selections=("papa@gmail.com",))
    other = make_family(db_session, name="Andere", tag="b")

    # B has no Google account of its own: nothing to modify, A's selection stays as it is
    response = client.put(CALENDARS, json={"calendars": []}, headers=headers_for(other["papa"]))

    assert response.status_code == 404
    assert _selection_rows(db_session, account) == {"papa@gmail.com": []}


# ------------------------------------------------------------------------------------------ sync now


def test_sync_now_imports_synchronously_and_returns_the_status(client, db_session, fam, configured, fake):
    account = make_account(db_session, fam["papa"], selections=("papa@gmail.com",), synced=False)
    fake.set_events("papa@gmail.com", [timed("e1", "Mia: Zahnarzt", soon())])

    response = client.post(SYNC_NOW, headers=headers_for(fam["papa"]))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["last_synced_at"] is not None and body["connected"] is True
    rows = db_session.scalars(select(CalendarEvent).where(CalendarEvent.sync_account_id == account.id)).all()
    assert [(r.title, r.owner_user_id) for r in rows] == [("Zahnarzt", fam["mia"].id)]


def test_sync_now_is_rate_limited_per_account_with_retry_after(client, db_session, fam, configured, fake, monkeypatch):
    make_account(db_session, fam["papa"], selections=("papa@gmail.com",))
    clock = {"now": 1000.0}
    monkeypatch.setattr(calendar_api, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    headers = headers_for(fam["papa"])

    assert client.post(SYNC_NOW, headers=headers).status_code == 200
    clock["now"] += 10
    limited = client.post(SYNC_NOW, headers=headers)
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "20"
    clock["now"] += 20
    assert client.post(SYNC_NOW, headers=headers).status_code == 200


def test_sync_now_rate_limit_is_independent_per_account(client, db_session, fam, configured, fake):
    make_account(db_session, fam["papa"], selections=("papa@gmail.com",))
    make_account(db_session, fam["mama"], selections=("papa@gmail.com",))

    assert client.post(SYNC_NOW, headers=headers_for(fam["papa"])).status_code == 200
    assert client.post(SYNC_NOW, headers=headers_for(fam["mama"])).status_code == 200


def test_sync_now_reports_needs_reauth(client, db_session, fam, configured, fake):
    make_account(db_session, fam["papa"], selections=("papa@gmail.com",))
    fake.errors["papa@gmail.com"] = http_error(401)

    body = client.post(SYNC_NOW, headers=headers_for(fam["papa"])).json()

    assert body["status"] == "needs_reauth" and body["last_error"]


def test_sync_now_without_account_is_404_and_never_touches_other_families(client, db_session, fam, configured, fake):
    account = make_account(db_session, fam["papa"], selections=("papa@gmail.com",), synced=False)
    other = make_family(db_session, name="Andere", tag="b")

    assert client.post(SYNC_NOW, headers=headers_for(other["papa"])).status_code == 404
    db_session.refresh(account)
    assert account.last_synced_at is None and fake.event_calls == []


# -------------------------------------------------------------------------------------- disconnect


def _imported_setup(db, fam):
    account = make_account(db, fam["papa"], selections=("cal-a",))
    other_account = make_account(db, fam["mama"], selections=("cal-a",))
    for title, acc in (("Importiert", account), ("Mamas Import", other_account)):
        db.add(CalendarEvent(
            family_id=fam["family"].id, title=title, start=soon(), end=soon(2), source="google",
            external_id=title, source_calendar_id="cal-a", sync_account_id=acc.id,
        ))
    db.add(CalendarEvent(family_id=fam["family"].id, title="Nativ", start=soon(), end=soon(2)))
    db.commit()
    return account, other_account


def test_disconnect_with_remove_events_deletes_only_this_accounts_imports(client, db_session, fam):
    account, other_account = _imported_setup(db_session, fam)
    account_id = account.id

    with patch("app.core.google_sync.httpx.post") as post:
        response = client.delete(DISCONNECT, params={"remove_events": "true"}, headers=headers_for(fam["papa"]))

    assert response.status_code == 204 and response.content == b""
    db_session.expire_all()
    assert db_session.get(CalendarSyncAccount, account_id) is None
    assert db_session.scalars(select(GoogleCalendarSelection).where(
        GoogleCalendarSelection.account_id == account_id)).all() == []
    assert {e.title for e in db_session.scalars(select(CalendarEvent))} == {"Mamas Import", "Nativ"}
    assert db_session.get(CalendarSyncAccount, other_account.id) is not None
    assert post.call_args.args[0] == "https://oauth2.googleapis.com/revoke"
    assert post.call_args.kwargs["data"] == {"token": "refresh-token-xyz"}


@pytest.mark.parametrize("query", [{"remove_events": "false"}, {}])
def test_disconnect_keeping_events_turns_them_into_plain_rows(client, db_session, fam, query):
    account, other_account = _imported_setup(db_session, fam)

    with patch("app.core.google_sync.httpx.post"):
        response = client.delete(DISCONNECT, params=query, headers=headers_for(fam["papa"]))

    assert response.status_code == 204
    db_session.expire_all()
    rows = {e.title: e for e in db_session.scalars(select(CalendarEvent))}
    assert set(rows) == {"Importiert", "Mamas Import", "Nativ"}
    assert rows["Importiert"].sync_account_id is None  # kept, but no longer managed by any sync
    assert rows["Mamas Import"].sync_account_id == other_account.id


def test_disconnect_ignores_a_failing_revoke(client, db_session, fam):
    account = make_account(db_session, fam["papa"])
    account_id = account.id

    with patch("app.core.google_sync.httpx.post", side_effect=RuntimeError("offline")):
        response = client.delete(DISCONNECT, headers=headers_for(fam["papa"]))

    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(CalendarSyncAccount, account_id) is None


def test_disconnect_is_404_without_an_account_and_cannot_remove_another_familys(client, db_session, fam):
    account, _ = _imported_setup(db_session, fam)
    other = make_family(db_session, name="Andere", tag="b")

    response = client.delete(DISCONNECT, params={"remove_events": "true"}, headers=headers_for(other["papa"]))

    assert response.status_code == 404
    db_session.expire_all()
    assert db_session.get(CalendarSyncAccount, account.id) is not None
    assert db_session.scalars(select(CalendarEvent)).all().__len__() == 3


# ------------------------------------------------------------------------------ connect and callback


def test_connect_unconfigured_is_503(client, fam, monkeypatch):
    monkeypatch.setattr(calendar_api, "get_settings", lambda: _settings(google_client_id=""))

    response = client.post(CONNECT, headers=headers_for(fam["papa"]))

    assert response.status_code == 503
    assert response.json()["detail"] == "Google-Kalender-Sync ist noch nicht konfiguriert."


def test_connect_uses_a_web_client_config_and_no_pkce(client, fam, configured):
    with patch("app.api.calendar.Flow") as flow_class:
        flow_class.from_client_config.return_value.authorization_url.return_value = ("https://x/auth", "s")
        response = client.post(CONNECT, headers=headers_for(fam["papa"]))

    assert response.status_code == 200
    config = flow_class.from_client_config.call_args.args[0]
    assert list(config) == ["web"] and config["web"]["redirect_uris"] == [_settings().google_redirect_uri]
    assert flow_class.from_client_config.call_args.kwargs["scopes"] == [
        "https://www.googleapis.com/auth/calendar.readonly"
    ]
    assert flow_class.from_client_config.call_args.kwargs["autogenerate_code_verifier"] is False


@pytest.mark.parametrize(("base_url", "secure"), [("https://app.example.com", True), ("http://localhost:8090", False)])
def test_connect_state_cookie_attributes(client, fam, monkeypatch, base_url, secure):
    monkeypatch.setattr(calendar_api, "get_settings", lambda: _settings(app_base_url=base_url))

    response = client.post(CONNECT, headers=headers_for(fam["papa"]))

    cookie = response.headers["set-cookie"].lower()
    assert "google_oauth_state=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "path=/calendar/sync/google/callback" in cookie and "max-age=600" in cookie
    assert ("secure" in cookie.replace("samesite", "")) is secure


def test_full_connect_then_callback_roundtrip_with_the_cookie_jar(client, db_session, fam, monkeypatch, fake):
    monkeypatch.setattr(calendar_api, "get_settings", lambda: _settings())
    fake.set_events("papa@gmail.com", [timed("e1", "Leo: Fussball", soon())])
    connect = client.post(CONNECT, headers=headers_for(fam["papa"]))
    state = parse_qs(urlparse(connect.json()["authorization_url"]).query)["state"][0]
    assert "code_challenge" not in connect.json()["authorization_url"]  # no PKCE: the callback has no verifier

    with patch("app.api.calendar.Flow") as flow_class:
        flow = flow_class.from_client_config.return_value
        flow.credentials = MagicMock(
            token="tok-1", refresh_token="ref-1", token_uri="https://oauth2.googleapis.com/token",
            client_id="client-id", client_secret="client-secret",
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
            expiry=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
        )
        # no cookies passed by hand: the browser-like jar must carry the state cookie from /connect
        response = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "http://localhost:8090/kalender?google=connected"
    account = db_session.scalar(select(CalendarSyncAccount).where(CalendarSyncAccount.user_id == fam["papa"].id))
    assert account.google_email == "papa@gmail.com" and account.status == "ok"
    stored = json.loads(decrypt_secret(account.oauth_token_encrypted))
    assert stored["expiry"] is not None and stored["refresh_token"] == "ref-1"
    selections = db_session.scalars(
        select(GoogleCalendarSelection).where(GoogleCalendarSelection.account_id == account.id)
    ).all()
    assert [(s.google_calendar_id, list(s.owner_user_ids)) for s in selections] == [("papa@gmail.com", [])]
    events = db_session.scalars(select(CalendarEvent).where(CalendarEvent.sync_account_id == account.id)).all()
    assert [(e.title, e.owner_user_id) for e in events] == [("Fussball", fam["leo"].id)]  # initial sync ran
    assert account.last_synced_at is not None


def _callback(client, db_session, user, creds=None, **kwargs):
    from app.core.security import create_oauth_state_token

    state = create_oauth_state_token(user.id)
    with patch("app.api.calendar.Flow") as flow_class:
        flow = flow_class.from_client_config.return_value
        flow.credentials = creds or MagicMock(
            token="tok-2", refresh_token="ref-2", token_uri="u", client_id="c", client_secret="s",
            scopes=["a"], expiry=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
        )
        for name, value in kwargs.items():
            setattr(flow, name, value)
        return client.get(
            CALLBACK, params={"code": "c", "state": state}, cookies={"google_oauth_state": state},
            follow_redirects=False,
        )


def test_callback_redirects_as_connected_even_when_the_initial_sync_fails(client, db_session, fam, configured):
    # the autouse guard makes any Google call fail: the best-effort initial sync must not break the redirect
    response = _callback(client, db_session, fam["papa"])

    assert response.headers["location"].endswith("/kalender?google=connected")
    assert db_session.scalar(select(CalendarSyncAccount).where(CalendarSyncAccount.user_id == fam["papa"].id))


def test_callback_reconnect_clears_needs_reauth_and_keeps_the_selection(client, db_session, fam, configured, fake):
    account = make_account(
        db_session, fam["papa"], selections=("schule@group.calendar.google.com",),
        status="needs_reauth", last_error="Bitte neu verbinden",
    )

    response = _callback(client, db_session, fam["papa"])

    assert response.headers["location"].endswith("/kalender?google=connected")
    db_session.refresh(account)
    assert account.status == "ok" and account.last_error is None
    assert _selection_rows(db_session, account) == {"schule@group.calendar.google.com": []}
    assert json.loads(decrypt_secret(account.oauth_token_encrypted))["token"] == "tok-2"


def test_callback_needs_a_parent_in_the_state_token(client, db_session, fam, configured):
    response = _callback(client, db_session, fam["mia"])

    assert "/kalender?google=error" in response.headers["location"]
    assert db_session.scalar(select(CalendarSyncAccount).where(CalendarSyncAccount.user_id == fam["mia"].id)) is None


def test_callback_without_the_state_cookie_fails_with_a_hint(client, fam, configured):
    from app.core.security import create_oauth_state_token

    state = create_oauth_state_token(fam["papa"].id)
    response = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)

    assert "google=error" in response.headers["location"] and "State-Cookie" in response.headers["location"]


# ------------------------------------------------------------------------------------------ redaction


def _event(db, fam, title, owner=None, *, source="google", private=False, external_id=None, start=None):
    start = start or soon()
    row = CalendarEvent(
        family_id=fam["family"].id, owner_user_id=owner.id if owner else None, title=title, start=start,
        end=start + timedelta(hours=1), source=source, is_private=private,
        external_id=external_id or title, source_calendar_id="cal-a" if source == "google" else None,
    )
    db.add(row)
    db.commit()
    return row


def _titles(client, headers):
    response = client.get("/calendar/events", headers=headers)
    assert response.status_code == 200
    return {(e["title"], e["owner_user_id"]) for e in response.json()}


@pytest.fixture()
def matrix(db_session, fam):
    ids = {k: str(fam[k].id) for k in ("papa", "mama", "mia", "leo")}
    _event(db_session, fam, "Privat-Familie", None, private=True)
    _event(db_session, fam, "Eltern-Termin", fam["papa"])
    _event(db_session, fam, "Kind-Termin", fam["mia"])
    _event(db_session, fam, "Familien-Termin", None)
    _event(db_session, fam, "Privates Kind", fam["leo"], private=True)
    _event(db_session, fam, "Papa und Mia", fam["papa"], external_id="shared-1")
    _event(db_session, fam, "Papa und Mia", fam["mia"], external_id="shared-1")
    _event(db_session, fam, "Papa und Mama", fam["papa"], external_id="shared-2")
    _event(db_session, fam, "Papa und Mama", fam["mama"], external_id="shared-2")
    _event(db_session, fam, "Nativ Papa", fam["papa"], source="native")
    _event(db_session, fam, "CalDAV Papa", fam["papa"], source="caldav")
    return ids


def test_parents_see_every_title(client, fam, matrix):
    titles = {title for title, _ in _titles(client, headers_for(fam["papa"]))}
    assert titles == {"Privat-Familie", "Eltern-Termin", "Kind-Termin", "Familien-Termin", "Privates Kind",
                      "Papa und Mia", "Papa und Mama", "Nativ Papa", "CalDAV Papa"}
    assert "Belegt" not in titles
    assert _titles(client, headers_for(fam["mama"])) == _titles(client, headers_for(fam["papa"]))


@pytest.mark.parametrize("who", ["mia", "wall"])
def test_children_and_the_dashboard_get_belegt_for_private_and_parent_only_events(client, fam, matrix, who):
    seen = _titles(client, headers_for(fam[who]))

    expected = {
        ("Belegt", None),  # private whole-family event
        ("Belegt", matrix["papa"]),  # google event of a parent only (Eltern-Termin, Papa und Mama)
        ("Belegt", matrix["mama"]),
        ("Kind-Termin", matrix["mia"]),  # belongs to a child
        ("Familien-Termin", None),  # whole family
        ("Belegt", matrix["leo"]),  # private event of a child
        ("Papa und Mia", matrix["papa"]),  # multi-owner: one owner is a child, so the event is visible
        ("Papa und Mia", matrix["mia"]),
        ("Nativ Papa", matrix["papa"]),  # only imported Google events are redacted
        ("CalDAV Papa", matrix["papa"]),
    }
    assert seen == expected


def test_multi_owner_event_of_only_parents_is_hidden_on_every_row(client, fam, matrix):
    response = client.get("/calendar/events", headers=headers_for(fam["mia"])).json()
    rows = [e for e in response if e["owner_user_id"] in (matrix["papa"], matrix["mama"]) and e["source"] == "google"]
    parent_only = [e for e in rows if e["title"] != "Papa und Mia"]
    assert len(parent_only) == 3 and {e["title"] for e in parent_only} == {"Belegt"}  # Eltern-Termin, Papa+Mama x2
    assert not any("Papa und Mama" in e["title"] for e in response)


def test_redaction_is_only_in_the_response_not_in_the_database(client, db_session, fam, matrix):
    client.get("/calendar/events", headers=headers_for(fam["mia"]))
    db_session.expire_all()

    assert db_session.scalars(select(CalendarEvent).where(CalendarEvent.title == "Belegt")).all() == []
    assert "Eltern-Termin" in _titles_flat(client, headers_for(fam["papa"]))


def _titles_flat(client, headers):
    return {title for title, _ in _titles(client, headers)}


def test_redacted_events_keep_their_times_and_other_fields_but_leak_no_title_elsewhere(client, db_session, fam):
    _event(db_session, fam, "Geheimer Arzttermin", fam["papa"], private=True)

    body = client.get("/calendar/events", headers=headers_for(fam["wall"])).json()

    assert len(body) == 1 and body[0]["title"] == "Belegt" and body[0]["start"] and body[0]["end"]
    assert "Geheimer Arzttermin" not in json.dumps(body)
    assert "is_private" not in body[0] and "external_id" not in body[0]


def test_events_of_other_families_are_never_returned(client, db_session, fam):
    other = make_family(db_session, name="Andere", tag="b")
    _event(db_session, other, "Fremd", None)
    _event(db_session, fam, "Eigen", None)

    assert _titles_flat(client, headers_for(fam["mia"])) == {"Eigen"}


def test_only_the_events_endpoints_return_calendar_events():
    """Redaction lives in GET /calendar/events: no other route may return CalendarEventRead (audit guard).

    POST /calendar/events returns the event the caller just created (native, never redacted).
    """
    from app.main import app

    returning = set()
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            if "CalendarEventRead" in json.dumps(operation.get("responses", {})):
                returning.add((method.upper(), path))
    assert returning == {("GET", "/calendar/events"), ("POST", "/calendar/events")}


def test_redacts_title_helper_matrix():
    parent_id, child_id = uuid.uuid4(), uuid.uuid4()
    roles = {parent_id: google_sync.UserRole.PARENT_ADMIN, child_id: google_sync.UserRole.CHILD}

    def row(owner, source="google", private=False):
        return SimpleNamespace(source=source, is_private=private, owner_user_id=owner, sync_account_id=None,
                               source_calendar_id="c", external_id=str(owner), start=None)

    assert google_sync.redacts_title(row(parent_id), roles, {}) is True
    assert google_sync.redacts_title(row(child_id), roles, {}) is False
    assert google_sync.redacts_title(row(None), roles, {}) is False
    assert google_sync.redacts_title(row(None, private=True), roles, {}) is True
    assert google_sync.redacts_title(row(parent_id, source="native"), roles, {}) is False
