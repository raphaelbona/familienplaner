"""Shared helpers for the Google calendar sync tests: a fake Google API service (no network), a family with
parents/children/wall dashboard, and sync accounts with valid (non-expired) stored credentials."""

import json
from datetime import UTC, date, datetime, timedelta

import httplib2
from googleapiclient.errors import HttpError

from app.core.crypto import encrypt_secret
from app.core.security import create_access_token
from app.models.calendar import CalendarSyncAccount, GoogleCalendarSelection
from app.models.family import Family
from app.models.user import User, UserRole

PRIMARY_ID = "papa@gmail.com"


class _Request:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class _CalendarList:
    def __init__(self, fake):
        self._fake = fake

    def list(self, **kwargs):
        return _Request(lambda: self._fake.run_calendar_list(kwargs))


class _Events:
    def __init__(self, fake):
        self._fake = fake

    def list(self, **kwargs):
        return _Request(lambda: self._fake.run_events(kwargs))


class FakeGoogle:
    """Stands in for ``googleapiclient`` calendar v3 resource: calendarList().list() and events().list()."""

    def __init__(self, calendars=None):
        self.calendars = (
            calendars
            if calendars is not None
            else [{"id": PRIMARY_ID, "summary": "Papa", "primary": True}]
        )
        self.pages: dict[str, list[list[dict]]] = {}
        self.errors: dict[str, Exception] = {}
        self.calendar_list_error: Exception | None = None
        self.event_calls: list[dict] = []

    def set_events(self, calendar_id, events, pages=1):
        size = max(1, -(-len(events) // pages)) if events else 1
        chunks = [events[i : i + size] for i in range(0, len(events), size)] or [[]]
        self.pages[calendar_id] = chunks

    def calendarList(self):  # noqa: N802 - mirrors the Google client
        return _CalendarList(self)

    def events(self):
        return _Events(self)

    def run_calendar_list(self, kwargs):
        if self.calendar_list_error:
            raise self.calendar_list_error
        return {"items": self.calendars}

    def run_events(self, kwargs):
        self.event_calls.append(kwargs)
        calendar_id = kwargs["calendarId"]
        if calendar_id in self.errors:
            raise self.errors[calendar_id]
        pages = self.pages.get(calendar_id, [[]])
        index = int(kwargs["pageToken"][1:]) if kwargs.get("pageToken") else 0
        body = {"items": pages[index]}
        if index + 1 < len(pages):
            body["nextPageToken"] = f"p{index + 1}"
        return body


def http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b'{"error": {"message": "x"}}')


def timed(event_id, summary, start: datetime, hours=1, **extra):
    return {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(hours=hours)).isoformat()},
        **extra,
    }


def all_day(event_id, summary, first: date, exclusive_end: date, **extra):
    return {
        "id": event_id,
        "summary": summary,
        "start": {"date": first.isoformat()},
        "end": {"date": exclusive_end.isoformat()},
        **extra,
    }


def soon(hours=48) -> datetime:
    return datetime.now(UTC).replace(microsecond=0) + timedelta(hours=hours)


def valid_creds_json(**overrides) -> str:
    data = {
        "token": "access-token-abc",
        "refresh_token": "refresh-token-xyz",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": ["https://www.googleapis.com/auth/calendar.readonly"],
        "expiry": (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None).isoformat(),
        **overrides,
    }
    return json.dumps(data)


def make_family(db, name="Muster", tag=""):
    """Family with parents Papa/Mama (admin/member), children Mia/Leo and the wall dashboard 'Wand'."""
    family = Family(name=name)
    db.add(family)
    db.commit()

    def add(display_name, role, email=None):
        user = User(family_id=family.id, role=role, display_name=display_name, email=email)
        db.add(user)
        db.commit()
        return user

    members = {
        "papa": add("Papa", UserRole.PARENT_ADMIN, f"papa{tag}@example.ch"),
        "mama": add("Mama", UserRole.PARENT_MEMBER, f"mama{tag}@example.ch"),
        "mia": add("Mia", UserRole.CHILD),
        "leo": add("Leo", UserRole.CHILD),
        "wall": add("Wand", UserRole.DASHBOARD),
    }
    members["family"] = family
    return members


def headers_for(user: User) -> dict:
    token = create_access_token(user.id, user.family_id, user.role.value)
    return {"Authorization": f"Bearer {token}"}


def make_account(db, user, *, selections=("primary",), owners=None, synced=True, **fields):
    """A connected Google account with a valid access token and the given calendar selections.

    ``selections`` are calendar ids; ``owners`` maps a calendar id to its default owner ids.
    """
    account = CalendarSyncAccount(
        user_id=user.id,
        provider="google",
        oauth_token_encrypted=encrypt_secret(valid_creds_json()),
        google_email=PRIMARY_ID,
        last_synced_at=datetime.now(UTC) - timedelta(hours=1) if synced else None,
        **fields,
    )
    db.add(account)
    db.commit()
    for calendar_id in selections:
        db.add(
            GoogleCalendarSelection(
                account_id=account.id,
                google_calendar_id=calendar_id,
                name=f"Kalender {calendar_id}",
                owner_user_ids=list((owners or {}).get(calendar_id, [])),
            )
        )
    db.commit()
    return account


def sync(db, account, fake, now=None, **kwargs):
    from app.core import google_sync

    return google_sync.sync_account(
        db,
        account,
        credentials_factory=kwargs.pop("credentials_factory", lambda _a, _d: object()),
        service_factory=lambda _c: fake,
        now=now,
        **kwargs,
    )
