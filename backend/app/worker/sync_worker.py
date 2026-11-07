"""Läuft als eigener Prozess (siehe docker-compose: Dienst 'worker').

Entkoppelt langsame externe Kalender-Aufrufe von der API, siehe Blueprint
Abschnitt 04 (Architektur) und 07 (Sync-Strategie).
"""

import json
import logging
from datetime import UTC, date, datetime, timedelta

import caldav
from apscheduler.schedulers.blocking import BlockingScheduler
from google.oauth2.credentials import Credentials
from googleapiclient import discovery
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import google_sync
from app.core.calendar_recurrence import generate_occurrences_for_series
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.network_guard import UnsafeUrlError, validate_external_server_url
from app.core.task_recurrence import (
    expire_stale_instances,
    generate_instances_for_assignment,
    prune_mismatched_instances,
)
from app.core.timetable_calendar import extend_timetable_calendar_events
from app.db.session import SessionLocal
from app.models.calendar import CalendarEvent, CalendarSyncAccount
from app.models.task import TaskAssignment, TaskTemplate
from app.models.timetable import TimetableEntry
from app.models.user import User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sync-worker")

settings = get_settings()


def _build_and_refresh_credentials(account: CalendarSyncAccount, db: Session) -> Credentials | None:
    """Credentials of a Google account, refreshed (and persisted) when expired.

    Raises google_sync.NeedsReauth when the stored token is unreadable or the refresh token was revoked.
    """
    return google_sync.load_credentials(account, db)


def _build_google_service(creds: Credentials) -> object:
    return discovery.build("calendar", "v3", credentials=creds, cache_discovery=False)


def sync_google_calendars() -> None:
    """Import the selected Google calendars of every connected account (see app.core.google_sync).

    Accounts that need a new authorization are skipped until the parent connects again.
    """
    db: Session = SessionLocal()
    try:
        accounts = db.scalars(
            select(CalendarSyncAccount).where(
                CalendarSyncAccount.provider == "google",
                CalendarSyncAccount.status != google_sync.STATUS_NEEDS_REAUTH,
            )
        ).all()
        for account in accounts:
            try:
                status = google_sync.sync_account(
                    db,
                    account,
                    credentials_factory=_build_and_refresh_credentials,
                    service_factory=_build_google_service,
                )
                logger.info("Google sync of account %s finished with status %s", account.id, status)
            except Exception as e:
                # sync_account never raises by design; this keeps one broken account from stopping the others.
                logger.error("Error syncing account %s: %s", account.id, type(e).__name__)
    finally:
        db.close()


def sync_caldav_calendars() -> None:
    """Sync events from CalDAV accounts (iCloud, Nextcloud, etc.)."""
    db: Session = SessionLocal()
    try:
        accounts = db.scalars(select(CalendarSyncAccount).where(CalendarSyncAccount.provider == "caldav"))
        for account in accounts:
            try:
                if not account.oauth_token_encrypted or not account.calendar_ref:
                    logger.warning("Skipping account %s: missing encrypted token or calendar ref", account.id)
                    continue

                # Decrypt credentials
                try:
                    creds_json = decrypt_secret(account.oauth_token_encrypted)
                    creds_dict = json.loads(creds_json)
                except Exception as e:
                    logger.error("Failed to decrypt credentials for account %s: %s", account.id, e)
                    continue

                server_url = creds_dict.get("server_url")
                username = creds_dict.get("username")
                password = creds_dict.get("password")

                if not all([server_url, username, password]):
                    logger.warning("Skipping account %s: incomplete credentials", account.id)
                    continue

                # Re-validate on every sync (defense in depth against DNS changes since connect-time)
                try:
                    validate_external_server_url(server_url, settings)
                except UnsafeUrlError as e:
                    logger.error("Skipping account %s: server URL no longer allowed: %s", account.id, e)
                    continue

                # Build CalDAV client and get calendar
                try:
                    client = caldav.DAVClient(
                        url=server_url,
                        username=username,
                        password=password,
                        timeout=10,
                    )
                    calendar = caldav.Calendar(client=client, url=account.calendar_ref)
                except Exception as e:
                    logger.error("Failed to connect to CalDAV server for account %s: %s", account.id, e)
                    continue

                # Get the user to find family_id
                user = db.get(User, account.user_id)
                if not user:
                    logger.warning("Skipping account %s: user not found", account.id)
                    continue

                # Search for events in the next 90 days
                now = datetime.now(UTC)
                search_start = now
                search_end = now + timedelta(days=90)

                try:
                    events = calendar.date_search(start=search_start, end=search_end, expand=True)
                except Exception as e:
                    logger.error("Failed to search events for account %s: %s", account.id, e)
                    continue

                logger.info("Fetched %d events for account %s", len(events), account.id)

                for event_resource in events:
                    try:
                        ical = event_resource.icalendar_component
                        if not ical:
                            logger.debug("Skipping event with no icalendar component")
                            continue

                        # Extract event details from iCalendar
                        uid = str(ical.get("UID", ""))
                        summary = str(ical.get("SUMMARY", "(Ohne Titel)"))
                        dtstart = ical.get("DTSTART")
                        dtend = ical.get("DTEND")

                        if not dtstart or not uid:
                            logger.debug("Skipping event with missing DTSTART or UID")
                            continue

                        # Handle start datetime
                        start_dt = dtstart.dt
                        if isinstance(start_dt, date) and not isinstance(start_dt, datetime):
                            # All-day event (date only)
                            start = datetime.combine(
                                start_dt, datetime.min.time()
                            ).replace(tzinfo=UTC)
                            all_day = True
                        else:
                            # Timed event
                            if isinstance(start_dt, datetime):
                                start = start_dt
                            else:
                                start = datetime.combine(
                                    start_dt, datetime.min.time()
                                ).replace(tzinfo=UTC)
                            all_day = False

                        # Handle end datetime
                        if dtend:
                            end_dt = dtend.dt
                            if isinstance(end_dt, date) and not isinstance(
                                end_dt, datetime
                            ):
                                # All-day event
                                end = datetime.combine(
                                    end_dt, datetime.min.time()
                                ).replace(tzinfo=UTC)
                            else:
                                if isinstance(end_dt, datetime):
                                    end = end_dt
                                else:
                                    end = datetime.combine(
                                        end_dt, datetime.min.time()
                                    ).replace(tzinfo=UTC)
                        else:
                            # No end time, use start + 1 hour
                            end = start + timedelta(hours=1)

                        # Upsert the event
                        stmt = select(CalendarEvent).where(
                            (CalendarEvent.family_id == user.family_id)
                            & (CalendarEvent.source == "caldav")
                            & (CalendarEvent.external_id == uid)
                        )
                        calendar_event = db.scalar(stmt)

                        if calendar_event:
                            calendar_event.title = summary
                            calendar_event.start = start
                            calendar_event.end = end
                            calendar_event.all_day = all_day
                        else:
                            calendar_event = CalendarEvent(
                                family_id=user.family_id,
                                title=summary,
                                start=start,
                                end=end,
                                all_day=all_day,
                                source="caldav",
                                external_id=uid,
                            )
                            db.add(calendar_event)

                    except Exception as e:
                        logger.error("Error processing event for account %s: %s", account.id, e)
                        continue

                account.last_synced_at = datetime.now(UTC)
                db.commit()
                logger.info("Synced events for CalDAV account %s", account.id)

            except Exception as e:
                logger.error("Error syncing CalDAV account %s: %s", account.id, e)
                db.rollback()
                continue
    finally:
        db.close()


def generate_timetable_events() -> None:
    """Rolls the timetable-derived calendar window forward as time passes.

    New occurrences are appended (see app.core.timetable_calendar); existing/past
    events are never touched here, so re-uploading a timetable stays the only way
    to change already-generated entries.
    """
    db: Session = SessionLocal()
    try:
        children = db.execute(select(TimetableEntry.family_id, TimetableEntry.child_user_id).distinct()).all()
        for family_id, child_user_id in children:
            try:
                extend_timetable_calendar_events(db, family_id, child_user_id)
            except Exception as e:
                logger.error("Error extending timetable calendar events for child %s: %s", child_user_id, e)
                db.rollback()
    finally:
        db.close()


def generate_recurring_task_instances() -> None:
    """Tops up the rolling window of due dates for every daily/weekly task assignment.

    Per family: open instances that no longer fit their template's day phases (or the day type of their date,
    e.g. after a school break) are removed first, then every assignment is topped up, then missed tasks of
    templates without carry_over expire.
    """
    db: Session = SessionLocal()
    try:
        family_ids = db.scalars(
            select(TaskAssignment.family_id)
            .join(TaskTemplate, TaskAssignment.template_id == TaskTemplate.id)
            .where(TaskTemplate.archived_at.is_(None))
            .distinct()
        ).all()
        for family_id in family_ids:
            try:
                prune_mismatched_instances(db, family_id)
                assignments = db.scalars(
                    select(TaskAssignment)
                    .join(TaskTemplate, TaskAssignment.template_id == TaskTemplate.id)
                    .where(TaskAssignment.family_id == family_id, TaskTemplate.archived_at.is_(None))
                ).all()
                for assignment in assignments:
                    try:
                        generate_instances_for_assignment(db, assignment)
                    except Exception as e:
                        logger.error("Error generating task instances for assignment %s: %s", assignment.id, e)
                        db.rollback()
                expire_stale_instances(db, family_id)
                db.commit()
            except Exception as e:
                logger.error("Error maintaining task instances of family %s: %s", family_id, e)
                db.rollback()
    finally:
        db.close()


def generate_recurring_calendar_events() -> None:
    """Tops up the rolling window of occurrences for every daily/weekly calendar event series."""
    db: Session = SessionLocal()
    try:
        roots = db.scalars(
            select(CalendarEvent).where(
                CalendarEvent.recurrence_rule.isnot(None), CalendarEvent.series_id.is_(None)
            )
        ).all()
        for root in roots:
            try:
                generate_occurrences_for_series(db, root)
            except Exception as e:
                logger.error("Error generating calendar occurrences for event %s: %s", root.id, e)
                db.rollback()
    finally:
        db.close()


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler()
    # Every job runs once shortly after the worker starts (staggered), then on its interval. Never pass
    # next_run_time=None: in APScheduler 3 that adds the job *paused*, i.e. it would never run. Starting
    # soon matters because the worker restarts on every deployment, more often than the 6 hour intervals.
    start = datetime.now(UTC)
    jobs = [
        (sync_google_calendars, {"minutes": settings.google_sync_poll_minutes}),
        (sync_caldav_calendars, {"minutes": settings.calendar_sync_poll_minutes}),
        (generate_timetable_events, {"hours": 6}),
        (generate_recurring_task_instances, {"hours": 6}),
        (generate_recurring_calendar_events, {"hours": 6}),
    ]
    for index, (func, interval) in enumerate(jobs):
        scheduler.add_job(
            func,
            "interval",
            next_run_time=start + timedelta(seconds=30 + 20 * index),
            **interval,
        )
    return scheduler


def main() -> None:
    scheduler = build_scheduler()
    logger.info(
        "Sync-Worker gestartet, Intervalle: Google %s Minuten, übrige Kalender-Syncs %s Minuten",
        settings.google_sync_poll_minutes,
        settings.calendar_sync_poll_minutes,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
