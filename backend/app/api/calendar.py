import json
import math
import time
import uuid
from typing import Annotated
from urllib.parse import quote

import caldav
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_parent
from app.core import google_sync
from app.core.calendar_recurrence import generate_occurrences_for_series
from app.core.config import get_settings
from app.core.crypto import encrypt_secret
from app.core.network_guard import UnsafeUrlError, validate_external_server_url
from app.core.security import create_oauth_state_token, decode_oauth_state_token
from app.core.task_recurrence import reconcile_family_task_instances_safely
from app.core.timetable_calendar import reconcile_family_timetables
from app.db.session import get_db
from app.models.calendar import CalendarEvent, CalendarSyncAccount, GoogleCalendarSelection
from app.models.school_break import SchoolBreak
from app.models.user import User, UserRole
from app.schemas.calendar import (
    CalDAVConnectRequest,
    CalendarEventCreate,
    CalendarEventRead,
    GoogleCalendarInfo,
    GoogleCalendarsUpdate,
    GoogleConnectResponse,
    GoogleStatusRead,
)

router = APIRouter(prefix="/calendar", tags=["calendar"])

PARENT_ROLES = (UserRole.PARENT_ADMIN, UserRole.PARENT_MEMBER)
GOOGLE_NOT_CONFIGURED = "Google-Kalender-Sync ist noch nicht konfiguriert."
GOOGLE_NOT_CONNECTED = "Google-Kalender ist nicht verbunden."
GOOGLE_LIST_FAILED = "Die Kalenderliste konnte nicht von Google geladen werden."
SYNC_NOW_MIN_INTERVAL_SECONDS = 30.0
# Last "Jetzt synchronisieren" per account (monotonic seconds). Per process, which is enough to stop hammering.
_last_sync_now: dict[uuid.UUID, float] = {}


@router.get("/events", response_model=list[CalendarEventRead])
def list_events(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[CalendarEventRead]:
    """All events of the family.

    This is the only endpoint that returns calendar events, so the redaction for viewers who are not parents
    (children, wall dashboard) happens here, in the response itself: imported Google events that are private or
    belong only to parents show just "Belegt".
    """
    stmt = select(CalendarEvent).where(CalendarEvent.family_id == current_user.family_id).order_by(CalendarEvent.start)
    rows = list(db.scalars(stmt))
    events = [CalendarEventRead.model_validate(row) for row in rows]
    if current_user.role in PARENT_ROLES:
        return events

    roles = dict(db.execute(select(User.id, User.role).where(User.family_id == current_user.family_id)).all())
    event_owners: dict[tuple, set[uuid.UUID | None]] = {}
    for row in rows:
        if row.source == "google":
            key = (row.sync_account_id, row.source_calendar_id, row.external_id, row.start)
            event_owners.setdefault(key, set()).add(row.owner_user_id)
    return [
        event.model_copy(update={"title": google_sync.REDACTED_TITLE})
        if google_sync.redacts_title(row, roles, event_owners)
        else event
        for row, event in zip(rows, events, strict=True)
    ]


@router.post("/events", response_model=CalendarEventRead, status_code=status.HTTP_201_CREATED)
def create_event(
    payload: CalendarEventCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CalendarEvent:
    owner_ids: list[uuid.UUID | None]
    if payload.owner_user_ids:
        owner_ids = []
        for member_id in payload.owner_user_ids:
            member = db.get(User, member_id)
            if member is None or member.family_id != current_user.family_id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Familienmitglied nicht gefunden")
            owner_ids.append(member_id)
    else:
        owner_ids = [payload.owner_user_id]

    created_roots: list[CalendarEvent] = []
    for owner_id in owner_ids:
        root = CalendarEvent(
            family_id=current_user.family_id,
            owner_user_id=owner_id,
            title=payload.title,
            start=payload.start,
            end=payload.end,
            all_day=payload.all_day,
            color=payload.color,
            source="native",
            recurrence_rule=payload.recurrence_rule,
        )
        db.add(root)
        db.commit()
        db.refresh(root)
        created_roots.append(root)

        if payload.recurrence_rule:
            generate_occurrences_for_series(db, root)

    return created_roots[0]


def _google_configured(settings) -> bool:
    return bool(settings.google_client_id and settings.google_client_secret and settings.google_redirect_uri)


def _google_flow(settings) -> Flow:
    # A "web" client (server-side redirect URI, client secret). The callback builds a fresh Flow, so no PKCE
    # code_verifier can be carried over to it: the signed state token + state cookie are the CSRF protection.
    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": google_sync.TOKEN_URI,
            "redirect_uris": [settings.google_redirect_uri],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=google_sync.SCOPES,
        redirect_uri=settings.google_redirect_uri,
        autogenerate_code_verifier=False,
    )


def _google_account(db: Session, user: User) -> CalendarSyncAccount | None:
    return db.scalar(
        select(CalendarSyncAccount).where(
            CalendarSyncAccount.user_id == user.id, CalendarSyncAccount.provider == "google"
        )
    )


@router.post("/sync/google/connect", response_model=GoogleConnectResponse)
def google_connect(
    current_user: Annotated[User, Depends(require_parent)], response: Response
) -> GoogleConnectResponse:
    settings = get_settings()
    if not _google_configured(settings):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GOOGLE_NOT_CONFIGURED)

    flow = _google_flow(settings)
    state = create_oauth_state_token(current_user.id)
    authorization_url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent", state=state
    )

    # State cookie for the CSRF check in the callback. In production the frontend (app.*) calls this endpoint on
    # another origin of the same site (api.*) with credentials: 'include', which is what lets the browser store
    # it. Google then sends the browser back to the callback with a top-level GET navigation, for which
    # SameSite=Lax cookies are included; the cookie is scoped to the callback path. Secure on https.
    response.set_cookie(
        "google_oauth_state",
        state,
        httponly=True,
        samesite="lax",
        secure=str(settings.app_base_url).startswith("https://"),
        max_age=600,
        path="/calendar/sync/google/callback",
    )

    return GoogleConnectResponse(authorization_url=authorization_url)


@router.get("/sync/google/callback")
def google_callback(
    code: str,
    state: str,
    db: Annotated[Session, Depends(get_db)],
    google_oauth_state: str | None = Cookie(default=None),
) -> RedirectResponse:
    """Google redirects the user's browser here directly, so every outcome -- success
    or failure -- ends in a redirect back into the frontend rather than raw JSON.
    """
    settings = get_settings()
    calendar_page_url = f"{settings.app_base_url}/kalender"

    def error_redirect(message: str) -> RedirectResponse:
        redirect = RedirectResponse(url=f"{calendar_page_url}?google=error&message={quote(message)}")
        redirect.delete_cookie("google_oauth_state", path="/calendar/sync/google/callback")
        return redirect

    # Validate state cookie for CSRF protection
    if not google_oauth_state or google_oauth_state != state:
        return error_redirect("Ungültige oder abgelaufene Sync-Anfrage (State-Cookie fehlt oder passt nicht).")

    try:
        user_id = decode_oauth_state_token(state)
    except ValueError:
        return error_redirect("Ungültiger oder abgelaufener State-Parameter.")

    user = db.get(User, user_id)  # user_id comes from our own signed state token
    if user is None or user.role not in PARENT_ROLES:
        return error_redirect("Ungültige oder abgelaufene Sync-Anfrage.")

    if not _google_configured(settings):
        return error_redirect(GOOGLE_NOT_CONFIGURED)

    flow = _google_flow(settings)
    try:
        flow.fetch_token(code=code)
    except Exception:
        return error_redirect("Ungültiger oder abgelaufener Autorisierungscode.")
    creds = flow.credentials

    encrypted_token = encrypt_secret(json.dumps(google_sync.credentials_to_dict(creds)))

    # Upsert CalendarSyncAccount (a reconnect keeps the calendar selection and clears "needs_reauth")
    account = _google_account(db, user)
    if account:
        account.oauth_token_encrypted = encrypted_token
        account.status = google_sync.STATUS_OK
        account.last_error = None
    else:
        account = CalendarSyncAccount(user_id=user.id, provider="google", oauth_token_encrypted=encrypted_token)
        db.add(account)
    db.commit()

    # Remember the Google account, preselect the primary calendar and import once. Best effort: whatever fails
    # here is retried by the worker, and the parent still lands on the calendar page as "connected".
    try:
        google_sync.setup_after_connect(db, account, creds)
    except Exception as exc:
        google_sync.logger.info("Initial Google setup failed (%s)", type(exc).__name__)

    redirect = RedirectResponse(url=f"{calendar_page_url}?google=connected")
    redirect.delete_cookie("google_oauth_state", path="/calendar/sync/google/callback")
    return redirect


def _live_calendars(db: Session, account: CalendarSyncAccount) -> tuple[list[dict] | None, str | None]:
    """The account's calendars straight from Google, or (None, message) when Google cannot be asked."""
    if account.status == google_sync.STATUS_NEEDS_REAUTH:
        return None, account.last_error or google_sync.MSG_NEEDS_REAUTH
    try:
        creds = google_sync.load_credentials(account, db)
        return google_sync.list_calendars(google_sync.build_service(creds)), None
    except Exception as exc:
        if google_sync.is_auth_failure(exc):
            account.status = google_sync.STATUS_NEEDS_REAUTH
            account.last_error = google_sync.MSG_NEEDS_REAUTH
            db.commit()
            return None, google_sync.MSG_NEEDS_REAUTH
        return None, GOOGLE_LIST_FAILED


def _selection_info(selection: GoogleCalendarSelection) -> GoogleCalendarInfo:
    return GoogleCalendarInfo(
        id=selection.google_calendar_id,
        name=selection.name,
        selected=True,
        owner_user_ids=list(selection.owner_user_ids or []),
    )


def _google_status(
    db: Session,
    account: CalendarSyncAccount | None,
    live: tuple[list[dict] | None, str | None] | None = None,
) -> GoogleStatusRead:
    configured = _google_configured(get_settings())
    if account is None:
        return GoogleStatusRead(configured=configured, connected=False)

    selections = {s.google_calendar_id: s for s in google_sync.get_selections(db, account.id)}
    live_calendars, live_error = live if live is not None else _live_calendars(db, account)

    calendars: list[GoogleCalendarInfo] = []
    if live_calendars is not None:
        seen: set[str] = set()
        for entry in live_calendars:
            # A selection stored as "primary" (accounts connected before the calendar picker) is the primary one.
            selection = selections.get(entry["id"]) or (selections.get("primary") if entry["primary"] else None)
            if selection is not None:
                seen.add(selection.google_calendar_id)
            calendars.append(
                GoogleCalendarInfo(
                    id=entry["id"],
                    name=entry["name"],
                    selected=selection is not None,
                    owner_user_ids=list(selection.owner_user_ids or []) if selection else [],
                )
            )
        calendars.sort(key=lambda c: not c.selected)  # stable: selected calendars first
        calendars.extend(_selection_info(s) for cid, s in selections.items() if cid not in seen)
    else:
        calendars = [_selection_info(s) for s in selections.values()]

    status_value, last_error = account.status, account.last_error
    if live_error and status_value == google_sync.STATUS_OK:
        status_value, last_error = google_sync.STATUS_ERROR, live_error
    return GoogleStatusRead(
        configured=configured,
        connected=True,
        email=account.google_email,
        status=status_value,
        last_synced_at=account.last_synced_at,
        last_error=last_error,
        calendars=calendars,
    )


@router.get("/sync/google/status", response_model=GoogleStatusRead)
def google_status(
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> GoogleStatusRead:
    return _google_status(db, _google_account(db, current_user))


@router.put("/sync/google/calendars", response_model=GoogleStatusRead)
def google_update_calendars(
    payload: GoogleCalendarsUpdate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> GoogleStatusRead:
    """Replace the selection: the listed calendars are imported (with their default owners), the rest is not."""
    account = _google_account(db, current_user)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, GOOGLE_NOT_CONNECTED)

    requested: dict[str, list[uuid.UUID]] = {}
    for item in payload.calendars:
        requested.setdefault(item.id, list(dict.fromkeys(item.owner_user_ids)))

    owner_ids = {owner for owners in requested.values() for owner in owners}
    if owner_ids:
        # Tenancy: every client-supplied member id must belong to the caller's family.
        known = set(
            db.scalars(select(User.id).where(User.id.in_(owner_ids), User.family_id == current_user.family_id))
        )
        if known != owner_ids:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Familienmitglied nicht gefunden")

    live = _live_calendars(db, account)
    live_names = {entry["id"]: entry["name"] for entry in live[0] or []}

    stored = {s.google_calendar_id: s for s in google_sync.get_selections(db, account.id)}
    for calendar_id, owners in requested.items():
        selection = stored.get(calendar_id)
        if selection is None:
            db.add(
                GoogleCalendarSelection(
                    account_id=account.id,
                    google_calendar_id=calendar_id,
                    name=live_names.get(calendar_id, calendar_id)[:200],
                    owner_user_ids=owners,
                )
            )
        else:
            selection.owner_user_ids = owners
            if calendar_id in live_names:
                selection.name = live_names[calendar_id][:200]
    for calendar_id, selection in stored.items():
        if calendar_id not in requested:
            db.delete(selection)
    # Events of calendars that are no longer imported go away with them.
    removed_break = google_sync.delete_account_events(db, account.id, keep_calendar_ids=list(requested))
    db.commit()
    if removed_break:
        reconcile_family_timetables(db, current_user.family_id)
        reconcile_family_task_instances_safely(db, current_user.family_id)
    db.refresh(account)
    return _google_status(db, account, live=live)


@router.post("/sync/google/sync-now", response_model=GoogleStatusRead)
def google_sync_now(
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> GoogleStatusRead:
    account = _google_account(db, current_user)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, GOOGLE_NOT_CONNECTED)

    now = time.monotonic()
    last = _last_sync_now.get(account.id)
    if last is not None and now - last < SYNC_NOW_MIN_INTERVAL_SECONDS:
        wait = math.ceil(SYNC_NOW_MIN_INTERVAL_SECONDS - (now - last))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Bitte kurz warten, bevor du erneut synchronisierst.",
            headers={"Retry-After": str(wait)},
        )
    _last_sync_now[account.id] = now

    google_sync.sync_account(db, account)
    db.refresh(account)
    return _google_status(db, account)


@router.delete("/sync/google", status_code=status.HTTP_204_NO_CONTENT)
def google_disconnect(
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
    remove_events: bool = False,
) -> Response:
    """Disconnect Google. The imported events are deleted (remove_events=true) or kept as plain events."""
    account = _google_account(db, current_user)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, GOOGLE_NOT_CONNECTED)

    google_sync.revoke_token(account)  # best effort, never raises

    imported = CalendarEvent.sync_account_id == account.id
    had_breaks = db.scalar(select(SchoolBreak.id).where(SchoolBreak.sync_account_id == account.id).limit(1)) is not None
    if remove_events:
        db.execute(delete(CalendarEvent).where(imported))
    else:
        db.execute(update(CalendarEvent).where(imported).values(sync_account_id=None))
    _last_sync_now.pop(account.id, None)
    db.delete(account)  # the selections and the imported school breaks go with it (ON DELETE CASCADE)
    db.commit()
    if had_breaks:
        reconcile_family_timetables(db, current_user.family_id)
        reconcile_family_task_instances_safely(db, current_user.family_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sync/caldav")
def caldav_connect(
    payload: CalDAVConnectRequest,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Connect to a CalDAV server (iCloud, Nextcloud, etc.) and sync calendars.

    This endpoint authenticates against the CalDAV server, lists available calendars,
    and stores the connection details encrypted in the database.
    """
    settings = get_settings()
    try:
        validate_external_server_url(payload.server_url, settings)
    except UnsafeUrlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    try:
        client = caldav.DAVClient(
            url=payload.server_url,
            username=payload.username,
            password=payload.password,
            timeout=10,
        )
        principal = client.principal()
    except Exception as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Verbindung fehlgeschlagen, bitte Server-URL und Zugangsdaten prüfen.",
        ) from e

    try:
        calendars = principal.calendars()
    except Exception as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Keine Kalender auf diesem Server gefunden.",
        ) from e

    if not calendars:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Keine Kalender auf diesem Server gefunden.",
        )

    # Get the URL of the first calendar
    calendar_url = str(calendars[0].url)

    # Encrypt the credentials
    creds_dict = {
        "server_url": payload.server_url,
        "username": payload.username,
        "password": payload.password,
    }
    encrypted_token = encrypt_secret(json.dumps(creds_dict))

    # Upsert CalendarSyncAccount
    stmt = select(CalendarSyncAccount).where(
        (CalendarSyncAccount.user_id == current_user.id) & (CalendarSyncAccount.provider == "caldav")
    )
    account = db.scalar(stmt)

    if account:
        account.oauth_token_encrypted = encrypted_token
        account.calendar_ref = calendar_url
    else:
        account = CalendarSyncAccount(
            user_id=current_user.id,
            provider="caldav",
            oauth_token_encrypted=encrypted_token,
            calendar_ref=calendar_url,
        )
        db.add(account)

    db.commit()
    return {"detail": "CalDAV-Kalender verbunden."}
