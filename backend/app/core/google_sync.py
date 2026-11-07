"""One-way import Google Calendar -> family calendar (never writes back to Google).

Shared by the sync worker (every 15 minutes), the OAuth callback (initial sync) and the API ("Jetzt
synchronisieren"). Everything that talks to Google goes through a ``service`` object so tests can mock the
Google API; nothing here logs tokens, secrets or raw exception texts.

Mapping of an event to family members, highest priority first:
1. a tag in the title: leading ``Mia:`` / ``Mia, Leo:`` / ``Mia und Leo:`` or ``#Mia`` / ``@Mia`` anywhere
   (matched case-insensitively and exactly against the members' display names; unknown or ambiguous names are
   ignored and a tag that resolves to nobody is left in the title); resolved tags are stripped from the title
2. attendees whose e-mail equals a parent's e-mail
3. the default owners configured for the calendar
4. nobody = the whole family (one row without owner)

One Google event yields one ``calendar_event`` row per member (same pattern as native multi-owner events).

School holidays: an all-day event whose (tag-free) title contains "ferien" or starts with "Schulfrei" /
"Schule frei" / "Unterrichtsfrei" (see app.core.school_breaks.classify_break_title) is imported as a
``school_break`` row INSTEAD of a calendar event. Its children are the tagged / default members that are
children, otherwise every child. Private events are never turned into breaks. Google's event list returns
every event that overlaps the sync window, so multi-day breaks that started before the window are seen with
their real start and end; breaks that ended more than WINDOW_PAST_DAYS ago drop out and are cleaned up.
"""

import json
import logging
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import google.auth.transport.requests
import httpx
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient import discovery
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.school_breaks import (
    MAX_SPAN_DAYS,
    SOURCE_GOOGLE,
    affected_children,
    classify_break_title,
    exclusive_end_to_last_day,
)
from app.core.task_recurrence import reconcile_family_task_instances_safely
from app.core.timetable_calendar import reconcile_family_timetables
from app.models.calendar import CalendarEvent, CalendarSyncAccount, GoogleCalendarSelection
from app.models.school_break import SchoolBreak
from app.models.user import User, UserRole

logger = logging.getLogger("google-sync")

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"

WINDOW_PAST_DAYS = 7
WINDOW_FUTURE_DAYS = 90
MAX_PAGES = 20
PAGE_SIZE = 250
TITLE_LIMIT = 200
REDACTED_TITLE = "Belegt"
NO_TITLE = "(Ohne Titel)"

STATUS_OK = "ok"
STATUS_NEEDS_REAUTH = "needs_reauth"
STATUS_ERROR = "error"

MSG_NEEDS_REAUTH = "Der Google-Zugriff ist abgelaufen oder wurde widerrufen. Bitte neu verbinden."
MSG_GENERIC_ERROR = "Die Synchronisierung ist fehlgeschlagen. Bitte später erneut versuchen."
MSG_TRUNCATED = "Zu viele Termine: nur ein Teil wurde importiert."

PRIVATE_VISIBILITIES = {"private", "confidential"}
_REAUTH_ERRORS = ("invalid_grant", "unauthorized_client", "invalid_client", "access_denied")

CredentialsFactory = Callable[[CalendarSyncAccount, Session], Credentials | None]
ServiceFactory = Callable[[Credentials], object]


class NeedsReauth(Exception):
    """The refresh token is gone (revoked/expired): the parent has to connect again."""


# --------------------------------------------------------------------------------------------------------
# Credentials


def credentials_to_dict(creds: Credentials) -> dict:
    expiry = creds.expiry if isinstance(creds.expiry, datetime) else None
    if expiry is not None and expiry.tzinfo is not None:
        expiry = expiry.astimezone(UTC).replace(tzinfo=None)
    scopes = creds.scopes
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(scopes) if isinstance(scopes, list | tuple | set) else None,
        # google-auth keeps the expiry as naive UTC; persisting it makes ``creds.expired`` work after a reload
        "expiry": expiry.isoformat() if expiry else None,
    }


def credentials_from_dict(data: dict) -> Credentials:
    expiry = None
    raw_expiry = data.get("expiry")
    if raw_expiry:
        expiry = datetime.fromisoformat(raw_expiry)
        if expiry.tzinfo is not None:
            expiry = expiry.astimezone(UTC).replace(tzinfo=None)
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri") or TOKEN_URI,
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes") or SCOPES,
        expiry=expiry,
    )


def store_credentials(account: CalendarSyncAccount, creds: Credentials) -> None:
    account.oauth_token_encrypted = encrypt_secret(json.dumps(credentials_to_dict(creds)))


def _is_reauth_error(exc: Exception) -> bool:
    text = " ".join(str(arg) for arg in exc.args).lower()
    return any(marker in text for marker in _REAUTH_ERRORS)


def load_credentials(account: CalendarSyncAccount, db: Session) -> Credentials:
    """Decrypt the stored credentials and refresh them when expired (persisting the new access token).

    Raises NeedsReauth when the token cannot be read or the refresh token was revoked.
    """
    if not account.oauth_token_encrypted:
        raise NeedsReauth("no token")
    try:
        creds = credentials_from_dict(json.loads(decrypt_secret(account.oauth_token_encrypted)))
    except Exception as exc:
        raise NeedsReauth("unreadable token") from exc

    # A token stored without expiry (older connections) is treated as stale once.
    if creds.refresh_token and (creds.expiry is None or creds.expired):
        try:
            creds.refresh(google.auth.transport.requests.Request())
        except RefreshError as exc:
            if _is_reauth_error(exc):
                raise NeedsReauth("refresh token rejected") from exc
            raise
        store_credentials(account, creds)
        db.commit()
    return creds


def build_service(creds: Credentials) -> object:
    return discovery.build("calendar", "v3", credentials=creds, cache_discovery=False)


def revoke_token(account: CalendarSyncAccount) -> None:
    """Best effort: ask Google to invalidate the grant. Failures are ignored (the account is removed anyway)."""
    try:
        data = json.loads(decrypt_secret(account.oauth_token_encrypted or ""))
        token = data.get("refresh_token") or data.get("token")
        if not token:
            return
        httpx.post(REVOKE_URI, data={"token": token}, timeout=5.0)
    except Exception as exc:
        logger.info("Revoking the Google token failed (%s)", type(exc).__name__)


# --------------------------------------------------------------------------------------------------------
# Title tags -> members


@dataclass(frozen=True)
class Member:
    id: uuid.UUID
    name: str
    email: str | None
    is_parent: bool


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


def _unique_by_name(members: Sequence[Member]) -> dict[str, Member]:
    """name -> member, without names that two members share (ambiguous names are ignored)."""
    seen: dict[str, Member | None] = {}
    for member in members:
        key = _norm(member.name)
        seen[key] = None if key in seen else member
    return {key: member for key, member in seen.items() if member is not None and key}


_PREFIX = re.compile(r"^\s*(?P<names>[^:]{1,200}?)\s*:\s*(?P<rest>\S.*)$", re.DOTALL)
_NAME_SEPARATOR = re.compile(r"\s*,\s*|\s+und\s+", re.IGNORECASE)


def parse_title(summary: str, members: Sequence[Member]) -> tuple[str, list[uuid.UUID]]:
    """Split a Google summary into the family title and the members named by a tag (in order of appearance)."""
    by_name = _unique_by_name(members)
    owners: list[uuid.UUID] = []

    def add(member: Member) -> None:
        if member.id not in owners:
            owners.append(member.id)

    title = summary

    prefix = _PREFIX.match(title)
    if prefix:
        found = [by_name[key] for key in (_norm(t) for t in _NAME_SEPARATOR.split(prefix["names"])) if key in by_name]
        if found:
            for member in found:
                add(member)
            title = prefix["rest"]

    # #Name / @Name anywhere; longest names first so "#Anna Maria" is not read as "#Anna".
    for member in sorted(by_name.values(), key=lambda m: len(_norm(m.name)), reverse=True):
        pattern = r"(?<!\w)[#@]" + r"\s+".join(re.escape(part) for part in member.name.split()) + r"(?!\w)"
        title, count = re.subn(pattern, " ", title, flags=re.IGNORECASE)
        if count:
            add(member)

    title = re.sub(r"\s+", " ", title).strip()
    return title, owners


def sanitize_title(title: str | None) -> str:
    """One line, no control characters, at most 200 characters (the column limit)."""
    cleaned = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", title or "")).strip()
    return cleaned[:TITLE_LIMIT].rstrip() or NO_TITLE


# --------------------------------------------------------------------------------------------------------
# Event times


def get_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().app_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Zurich")


def _zone(name: str | None, fallback: tzinfo) -> tzinfo:
    if not name:
        return fallback
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return fallback


def _parse_datetime(data: dict, fallback: tzinfo) -> datetime:
    value = datetime.fromisoformat(data["dateTime"])
    if value.tzinfo is None:
        value = value.replace(tzinfo=_zone(data.get("timeZone"), fallback))
    return value


def _local_midnight(day: date, tz: tzinfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def event_times(event: dict, tz: tzinfo) -> tuple[datetime, datetime, bool] | None:
    """(start, end, all_day) with timezone-aware datetimes, or None for an unusable event.

    All-day events are anchored at local midnight in the app timezone. Google's ``end.date`` is exclusive and
    is stored as is (local midnight of the day after the last day) -- the same shape native all-day rows have
    (parent letters: start + 1 day), which the dashboards rely on (``end`` of a one-day event is after "now"
    for the whole day).
    """
    start_data = event.get("start") or {}
    end_data = event.get("end") or {}
    try:
        if "dateTime" in start_data:
            start = _parse_datetime(start_data, tz)
            end = _parse_datetime(end_data, tz) if "dateTime" in end_data else start + timedelta(hours=1)
            if end < start:
                end = start
            return start, end, False
        if "date" in start_data:
            first_day = date.fromisoformat(start_data["date"])
            start = _local_midnight(first_day, tz)
            end = _local_midnight(date.fromisoformat(end_data["date"]), tz) if "date" in end_data else None
            if end is None or end <= start:
                end = _local_midnight(first_day + timedelta(days=1), tz)
            return start, end, True
    except (ValueError, TypeError):
        return None
    return None


# --------------------------------------------------------------------------------------------------------
# Google API helpers


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def list_calendars(service: object) -> list[dict]:
    """The user's calendars: [{id, name, primary}] (paged calendarList)."""
    calendars: list[dict] = []
    token: str | None = None
    for _ in range(MAX_PAGES):
        params = {"maxResults": 250, "minAccessRole": "reader"}
        if token:
            params["pageToken"] = token
        response = _as_dict(service.calendarList().list(**params).execute())
        for item in response.get("items") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            calendars.append(
                {
                    "id": str(item["id"])[:200],
                    "name": sanitize_title(item.get("summaryOverride") or item.get("summary") or item["id"]),
                    "primary": bool(item.get("primary")),
                }
            )
        token = response.get("nextPageToken")
        if not token:
            break
    return calendars


def _primary(calendars: Sequence[dict]) -> dict | None:
    return next((c for c in calendars if c["primary"]), None)


def _fetch_events(service: object, calendar_id: str, now: datetime) -> tuple[list[dict], bool]:
    """All events of the sync window; the flag says whether every page was read."""
    time_min = (now - timedelta(days=WINDOW_PAST_DAYS)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = (now + timedelta(days=WINDOW_FUTURE_DAYS)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    events: list[dict] = []
    token: str | None = None
    for _ in range(MAX_PAGES):
        params = {
            "calendarId": calendar_id,
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "showDeleted": False,
            "maxResults": PAGE_SIZE,
            "orderBy": "startTime",
        }
        if token:
            params["pageToken"] = token
        response = _as_dict(service.events().list(**params).execute())
        events.extend(item for item in response.get("items") or [] if isinstance(item, dict))
        token = response.get("nextPageToken")
        if not token:
            return events, True
    return events, False


def _is_unauthorized(exc: HttpError) -> bool:
    return getattr(exc.resp, "status", None) == 401


def is_auth_failure(exc: Exception) -> bool:
    """Whether the exception means the parent has to authorize again (revoked/expired grant, HTTP 401)."""
    if isinstance(exc, NeedsReauth):
        return True
    if isinstance(exc, HttpError):
        return _is_unauthorized(exc)
    if isinstance(exc, RefreshError):
        return _is_reauth_error(exc)
    return False


# --------------------------------------------------------------------------------------------------------
# Applying events to the family calendar


def load_members(db: Session, family_id: uuid.UUID) -> list[Member]:
    users = db.scalars(select(User).where(User.family_id == family_id, User.role != UserRole.DASHBOARD))
    return [
        Member(
            id=user.id,
            name=user.display_name,
            email=(user.email or "").strip().lower() or None,
            is_parent=user.role in (UserRole.PARENT_ADMIN, UserRole.PARENT_MEMBER),
        )
        for user in users
    ]


def _owners_for(
    event: dict, tagged: list[uuid.UUID], members: Sequence[Member], defaults: Sequence[uuid.UUID]
) -> list[uuid.UUID | None]:
    if tagged:
        return list(tagged)
    parent_by_email = {m.email: m for m in members if m.is_parent and m.email}
    from_attendees: list[uuid.UUID] = []
    for attendee in event.get("attendees") or []:
        member = parent_by_email.get(str(_as_dict(attendee).get("email") or "").strip().lower())
        if member and member.id not in from_attendees:
            from_attendees.append(member.id)
    if from_attendees:
        return list(from_attendees)
    known = {m.id for m in members}
    configured = [owner for owner in defaults if owner in known]
    return list(dict.fromkeys(configured)) or [None]


BREAK_TITLE_LIMIT = 100


def _break_values(
    event: dict,
    title: str,
    tagged: list[uuid.UUID],
    times: tuple[datetime, datetime, bool],
    members: Sequence[Member],
    defaults: Sequence[uuid.UUID],
) -> dict | None:
    """The school_break columns for an all-day holiday event, None when the event is a normal event."""
    start, end, all_day = times
    if not all_day or event.get("visibility") in PRIVATE_VISIBILITIES:
        return None
    kind = classify_break_title(title)
    if kind is None:
        return None
    first_day = start.date()
    last_day = exclusive_end_to_last_day(first_day, end.date())
    if (last_day - first_day).days + 1 > MAX_SPAN_DAYS:
        return None
    children = {m.id for m in members if not m.is_parent}
    # Owners that are parents (or nobody) mean "all children": the list stays empty then.
    owners = [owner for owner in _owners_for(event, tagged, members, defaults) if owner in children]
    return {
        "title": sanitize_title(title)[:BREAK_TITLE_LIMIT].rstrip(),
        "kind": kind,
        "start_date": first_day,
        "end_date": last_day,
        "child_user_ids": owners,
    }


def _wanted_rows(
    events: Sequence[dict], members: Sequence[Member], defaults: Sequence[uuid.UUID], tz: tzinfo
) -> tuple[dict[tuple[str, uuid.UUID | None], dict], dict[str, dict]]:
    """(calendar rows by (external id, owner), school breaks by external id) wanted for the fetched events."""
    wanted: dict[tuple[str, uuid.UUID | None], dict] = {}
    breaks: dict[str, dict] = {}
    for event in events:
        external_id = event.get("id")
        if not external_id or event.get("status") == "cancelled" or len(str(external_id)) > 200:
            continue
        times = event_times(event, tz)
        if times is None:
            continue
        start, end, all_day = times
        title, tagged = parse_title(str(event.get("summary") or ""), members)
        school_break = _break_values(event, title, tagged, times, members, defaults)
        if school_break is not None:
            breaks[str(external_id)] = school_break
            continue
        values = {
            "title": sanitize_title(title),
            "start": start,
            "end": end,
            "all_day": all_day,
            "is_private": event.get("visibility") in PRIVATE_VISIBILITIES,
        }
        for owner in _owners_for(event, tagged, members, defaults):
            wanted[(str(external_id), owner)] = values
    return wanted, breaks


def _apply_breaks(
    db: Session,
    account_id: uuid.UUID,
    family_id: uuid.UUID,
    calendar_id: str,
    events: Sequence[dict],
    complete: bool,
    wanted_breaks: dict[str, dict],
) -> list[list[uuid.UUID]]:
    """Upsert/clean up the google school breaks of one calendar; returns the child lists of every change."""
    existing: dict[str | None, list[SchoolBreak]] = {}
    rows = db.scalars(
        select(SchoolBreak).where(
            SchoolBreak.family_id == family_id,
            SchoolBreak.source == SOURCE_GOOGLE,
            SchoolBreak.source_calendar_id == calendar_id,
        )
    )
    for row in rows:
        existing.setdefault(row.external_id, []).append(row)
    fetched_ids = {str(event["id"]) for event in events if event.get("id")}

    scopes: list[list[uuid.UUID]] = []
    for external_id, values in wanted_breaks.items():
        candidates = existing.get(external_id, [])
        mine = [r for r in candidates if r.sync_account_id == account_id]
        if candidates and not mine:
            continue  # another account already imports this event
        if mine:
            row, extras = mine[0], mine[1:]
            for extra in extras:
                scopes.append(list(extra.child_user_ids))
                db.delete(extra)
            if any(getattr(row, field) != value for field, value in values.items()):
                scopes.append(list(row.child_user_ids))
                scopes.append(list(values["child_user_ids"]))
                for field, value in values.items():
                    setattr(row, field, value)
        else:
            db.add(
                SchoolBreak(
                    family_id=family_id,
                    source=SOURCE_GOOGLE,
                    sync_account_id=account_id,
                    source_calendar_id=calendar_id,
                    external_id=external_id,
                    **values,
                )
            )
            scopes.append(list(values["child_user_ids"]))

    for external_id, candidates in existing.items():
        if external_id in wanted_breaks:
            continue
        # Gone from the window, cancelled, or no longer a holiday title. A truncated import only drops the
        # rows whose event it actually saw again.
        if not (complete or external_id in fetched_ids):
            continue
        for row in candidates:
            if row.sync_account_id == account_id:
                scopes.append(list(row.child_user_ids))
                db.delete(row)
    return scopes


def _apply_calendar(
    db: Session,
    account: CalendarSyncAccount,
    family_id: uuid.UUID,
    selection: GoogleCalendarSelection,
    events: Sequence[dict],
    complete: bool,
    members: Sequence[Member],
    tz: tzinfo,
) -> None:
    calendar_id = selection.google_calendar_id
    wanted, wanted_breaks = _wanted_rows(events, members, selection.owner_user_ids or [], tz)

    existing: dict[tuple[str | None, uuid.UUID | None], list[CalendarEvent]] = {}
    rows = db.scalars(
        select(CalendarEvent).where(
            CalendarEvent.family_id == family_id,
            CalendarEvent.source == "google",
            CalendarEvent.source_calendar_id == calendar_id,
        )
    )
    for row in rows:
        existing.setdefault((row.external_id, row.owner_user_id), []).append(row)

    account_id = account.id  # read once: touching an expired ORM object later would autoflush half-built rows
    scopes: list[list[uuid.UUID]] = []
    with db.begin_nested():
        scopes = _apply_breaks(db, account_id, family_id, calendar_id, events, complete, wanted_breaks)
        # An event that is a holiday now must not stay behind as a normal calendar event.
        for key, candidates in existing.items():
            if key[0] in wanted_breaks:
                for row in candidates:
                    if row.sync_account_id in (None, account_id):
                        db.delete(row)
        for key, values in wanted.items():
            candidates = existing.get(key, [])
            # Rows another account manages are left alone (no duplicates, no tug of war); rows of a
            # disconnected account (sync_account_id NULL) are adopted again.
            mine = [r for r in candidates if r.sync_account_id in (None, account_id)]
            if candidates and not mine:
                continue
            if mine:
                row, extras = mine[0], mine[1:]
                for extra in extras:
                    db.delete(extra)
                row.sync_account_id = account_id
                for field, value in values.items():
                    setattr(row, field, value)
            else:
                db.add(
                    CalendarEvent(
                        family_id=family_id,
                        source="google",
                        external_id=key[0],
                        source_calendar_id=calendar_id,
                        owner_user_id=key[1],
                        sync_account_id=account_id,
                        **values,
                    )
                )

        if complete:
            for key, candidates in existing.items():
                if key in wanted:
                    continue
                for row in candidates:
                    if row.sync_account_id == account_id:
                        db.delete(row)
    db.commit()

    if scopes:
        # The timetable events of the affected children follow the changed breaks.
        try:
            reconcile_family_timetables(db, family_id, affected_children(*scopes))
        except SQLAlchemyError as exc:
            logger.warning("Reconciling the timetable after a break import failed (%s)", type(exc).__name__)
            db.rollback()
        # The day type of the affected days changed: the phase tasks of the children follow.
        reconcile_family_task_instances_safely(db, family_id)


def delete_account_events(
    db: Session, account_id: uuid.UUID, keep_calendar_ids: Sequence[str] | None = None
) -> bool:
    """Remove the events and school breaks imported by this account (optionally only those of calendars not in
    keep_calendar_ids). Returns whether a school break was removed (the caller then reconciles the timetable)."""
    stmt = select(CalendarEvent).where(CalendarEvent.sync_account_id == account_id, CalendarEvent.source == "google")
    for row in db.scalars(stmt):
        if keep_calendar_ids is not None and row.source_calendar_id in keep_calendar_ids:
            continue
        db.delete(row)
    removed_break = False
    for brk in db.scalars(select(SchoolBreak).where(SchoolBreak.sync_account_id == account_id)):
        if keep_calendar_ids is not None and brk.source_calendar_id in keep_calendar_ids:
            continue
        db.delete(brk)
        removed_break = True
    return removed_break


def get_selections(db: Session, account_id: uuid.UUID) -> list[GoogleCalendarSelection]:
    stmt = (
        select(GoogleCalendarSelection)
        .where(GoogleCalendarSelection.account_id == account_id)
        .order_by(GoogleCalendarSelection.name, GoogleCalendarSelection.google_calendar_id)
    )
    return list(db.scalars(stmt))


def ensure_default_selection(db: Session, account: CalendarSyncAccount, service: object) -> None:
    """Preselect the primary calendar (for the whole family) of an account that was never synced."""
    if account.last_synced_at is not None or get_selections(db, account.id):
        return
    calendar_id, name = "primary", "Primärkalender"
    try:
        primary = _primary(list_calendars(service))
    except Exception as exc:
        logger.info("Listing Google calendars failed (%s)", type(exc).__name__)
        primary = None
    if primary:
        calendar_id, name = primary["id"], primary["name"]
        if "@" in calendar_id:
            account.google_email = calendar_id[:200]
    db.add(GoogleCalendarSelection(account_id=account.id, google_calendar_id=calendar_id, name=name, owner_user_ids=[]))
    db.commit()


def _mark(account: CalendarSyncAccount, status: str, message: str | None) -> None:
    account.status = status
    account.last_error = message[:300] if message else None


def sync_account(
    db: Session,
    account: CalendarSyncAccount,
    *,
    credentials_factory: CredentialsFactory | None = None,
    service_factory: ServiceFactory | None = None,
    now: datetime | None = None,
) -> str:
    """Import the selected calendars of one Google account and record the outcome on the account.

    Returns the resulting account status. Never raises: a failing calendar does not stop the others, an
    authorization failure marks the account ``needs_reauth`` and any other failure ``error`` (short German
    message without tokens or raw exception text).
    """
    now = now or datetime.now(UTC)
    account_id = account.id
    try:
        creds = (credentials_factory or load_credentials)(account, db)
        if creds is None:
            raise NeedsReauth("no credentials")
        service = (service_factory or build_service)(creds)

        user = db.get(User, account.user_id)
        if user is None:
            raise RuntimeError("account owner is gone")
        family_id = user.family_id

        ensure_default_selection(db, account, service)
        selections = get_selections(db, account.id)
        # Calendars the parent deselected: their imported events go away.
        delete_account_events(db, account.id, keep_calendar_ids=[s.google_calendar_id for s in selections])

        members = load_members(db, family_id)
        tz = get_timezone()
        failed: list[str] = []
        truncated = False
        succeeded = 0
        for selection in selections:
            try:
                events, complete = _fetch_events(service, selection.google_calendar_id, now)
            except HttpError as exc:
                if _is_unauthorized(exc):
                    raise NeedsReauth("unauthorized") from exc
                logger.info("Google calendar fetch failed (HTTP %s)", getattr(exc.resp, "status", "?"))
                failed.append(selection.name)
                continue
            except RefreshError as exc:
                if _is_reauth_error(exc):
                    raise NeedsReauth("refresh rejected") from exc
                failed.append(selection.name)
                continue
            except Exception as exc:
                logger.info("Google calendar fetch failed (%s)", type(exc).__name__)
                failed.append(selection.name)
                continue
            _apply_calendar(db, account, family_id, selection, events, complete, members, tz)
            succeeded += 1
            truncated = truncated or not complete

        if failed:
            _mark(account, STATUS_ERROR, "Nicht synchronisiert: " + ", ".join(failed))
        elif truncated:
            _mark(account, STATUS_ERROR, MSG_TRUNCATED)
        else:
            _mark(account, STATUS_OK, None)
        if succeeded or not selections:
            account.last_synced_at = now
        db.commit()
    except NeedsReauth:
        _mark(account, STATUS_NEEDS_REAUTH, MSG_NEEDS_REAUTH)
        db.commit()
    except SQLAlchemyError as exc:
        logger.warning("Google sync of account %s failed in the database (%s)", account_id, type(exc).__name__)
        db.rollback()
        _mark_after_failure(db, account_id, MSG_GENERIC_ERROR)
    except Exception as exc:
        logger.warning("Google sync of account %s failed (%s)", account_id, type(exc).__name__)
        _mark(account, STATUS_ERROR, MSG_GENERIC_ERROR)
        db.commit()
    return account.status


def _mark_after_failure(db: Session, account_id: uuid.UUID, message: str) -> None:
    account = db.get(CalendarSyncAccount, account_id)
    if account is not None:
        _mark(account, STATUS_ERROR, message)
        db.commit()


def setup_after_connect(
    db: Session,
    account: CalendarSyncAccount,
    creds: Credentials,
    *,
    service_factory: ServiceFactory | None = None,
) -> None:
    """After a (re)connect: remember the primary calendar's id as the e-mail, preselect it, import once."""
    service = (service_factory or build_service)(creds)
    primary = _primary(list_calendars(service))
    if primary and "@" in primary["id"]:
        account.google_email = primary["id"][:200]
    _mark(account, STATUS_OK, None)
    db.commit()
    sync_account(db, account, credentials_factory=lambda _a, _d: creds, service_factory=lambda _c: service)


# --------------------------------------------------------------------------------------------------------
# Redaction for viewers who are not parents


def redacts_title(
    row: CalendarEvent, roles: dict[uuid.UUID, UserRole], event_owners: dict[tuple, set[uuid.UUID | None]]
) -> bool:
    """Whether a non-parent viewer must see "Belegt" instead of this row's title (google events only).

    Private/confidential events are always redacted. Otherwise an event is hidden when every owner of the event
    (all rows of the same Google event) is a parent; whole-family events (no owner) and events of a child stay
    visible.
    """
    if row.source != "google":
        return False
    if row.is_private:
        return True
    key = (row.sync_account_id, row.source_calendar_id, row.external_id, row.start)
    owners = event_owners.get(key, {row.owner_user_id})
    if not owners or None in owners:
        return False
    return all(roles.get(owner) in (UserRole.PARENT_ADMIN, UserRole.PARENT_MEMBER) for owner in owners)
