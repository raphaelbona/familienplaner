"""Generates a rolling window of TaskInstance rows for a recurring TaskAssignment.

Mirrors app.core.timetable_calendar's rolling-window pattern: instead of a parent
re-assigning the same chore every day/week by hand, the upcoming due dates are
pre-generated and topped up periodically by the worker.

A template with day phases (app.models.day_phase) is split into one instance per applicable phase and day. A
missed instance of a template without ``carry_over`` expires once the next one is due (expire_stale_instances).
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.day_type import DayType, day_types_for_range, phase_applies
from app.models.day_phase import DayPhase
from app.models.task import TaskAssignment, TaskInstance, TaskStatus, TaskTemplate

logger = logging.getLogger(__name__)

DAILY_WINDOW_DAYS = 14
WEEKLY_WINDOW_DAYS = 56  # ~8 occurrences


def _weekday_set(assignment: TaskAssignment) -> set[int]:
    """Which weekdays (0=Mon..6=Sun) a 'weekly' assignment is due on.

    Defaults to start_date's own weekday (legacy behavior: exactly once per week)
    when no explicit weekdays were chosen, so old rows keep working unchanged.
    """
    if assignment.weekdays:
        return {int(d) for d in assignment.weekdays.split(",")}
    return {assignment.start_date.weekday()}


def _upcoming_due_dates(assignment: TaskAssignment, today: date) -> list[date]:
    """Due dates from today through the rolling window."""
    first = max(assignment.start_date, today)

    if assignment.recurrence_rule == "daily":
        window_end = today + timedelta(days=DAILY_WINDOW_DAYS)
        due_dates = []
        current = first
        while current <= window_end:
            due_dates.append(current)
            current += timedelta(days=1)
        return due_dates

    # "weekly": due on one or more chosen weekdays every week (e.g. "zweimal die Woche").
    window_end = today + timedelta(days=WEEKLY_WINDOW_DAYS)
    weekdays = _weekday_set(assignment)
    due_dates = []
    current = first
    while current <= window_end:
        if current.weekday() in weekdays:
            due_dates.append(current)
        current += timedelta(days=1)
    return due_dates


def phase_targets(
    phases: Sequence[DayPhase], day_types: dict[date, DayType], due_date: date
) -> list[uuid.UUID | None]:
    """The phase ids an instance exists for on this date: [None] without phases, [] when no phase applies."""
    if not phases:
        return [None]
    return [phase.id for phase in phases if phase_applies(phase, day_types[due_date])]


def day_types_if_phased(
    db: Session, assignment: TaskAssignment, phases: Sequence[DayPhase], dates: Sequence[date]
) -> dict[date, DayType]:
    """Day types of the dates for the assignee (empty when the template has no phases: they are not needed)."""
    if not phases or not dates:
        return {}
    return day_types_for_range(db, assignment.assignee_user_id, assignment.family_id, min(dates), max(dates))


def generate_instances_for_assignment(
    db: Session, assignment: TaskAssignment, today: date | None = None
) -> list[TaskInstance]:
    """Top up the rolling window of due TaskInstance rows for a recurring assignment.

    A template with day phases gets one instance per phase that applies to the assignee on that day (no
    instance on days where none applies); a template without phases one instance per day with phase_id None.
    """
    today = today or local_today()

    # An archived (deleted-but-still-referenced) template must not spawn new tasks.
    if assignment.template.archived_at is not None:
        return []

    phases = list(assignment.template.phases)
    due_dates = _upcoming_due_dates(assignment, today)
    day_types = day_types_if_phased(db, assignment, phases, due_dates)

    existing = {
        (due_date, phase_id)
        for due_date, phase_id in db.execute(
            select(TaskInstance.due_date, TaskInstance.phase_id).where(TaskInstance.assignment_id == assignment.id)
        )
    }

    created: list[TaskInstance] = []
    for due_date in due_dates:
        for phase_id in phase_targets(phases, day_types, due_date):
            if (due_date, phase_id) in existing:
                continue
            instance = TaskInstance(
                template_id=assignment.template_id,
                assignee_user_id=assignment.assignee_user_id,
                due_date=due_date,
                assignment_id=assignment.id,
                phase_id=phase_id,
            )
            db.add(instance)
            created.append(instance)

    db.commit()
    for instance in created:
        db.refresh(instance)
    return created


def _active_assignments(db: Session, family_id: uuid.UUID) -> list[TaskAssignment]:
    return list(
        db.scalars(
            select(TaskAssignment)
            .join(TaskTemplate, TaskAssignment.template_id == TaskTemplate.id)
            .where(TaskAssignment.family_id == family_id, TaskTemplate.archived_at.is_(None))
        )
    )


def _no_longer_fits(instance: TaskInstance, phases: Sequence[DayPhase], day_types: dict[date, DayType]) -> bool:
    """Whether an instance no longer matches the template's phases (or the day type of its date)."""
    if not phases:
        return instance.phase_id is not None
    return instance.phase_id not in phase_targets(phases, day_types, instance.due_date)


def prune_mismatched_instances(db: Session, family_id: uuid.UUID, today: date | None = None) -> int:
    """Delete the open, never completed instances from today on that no longer fit their template's phases.

    Fits means: the instance's phase is one of the template's phases and applies on that day for the assignee
    (day type of the date), or the template has no phases and the instance none either. Completed, approved
    and expired instances and one-off tasks are never touched. Commits, returns the number deleted.
    """
    today = today or local_today()
    deleted = 0
    for assignment in _active_assignments(db, family_id):
        phases = list(assignment.template.phases)
        candidates = db.scalars(
            select(TaskInstance).where(
                TaskInstance.assignment_id == assignment.id,
                TaskInstance.status == TaskStatus.OPEN,
                TaskInstance.completed_at.is_(None),
                TaskInstance.due_date >= today,
            )
        ).all()
        day_types = day_types_if_phased(db, assignment, phases, [i.due_date for i in candidates])
        for instance in candidates:
            if _no_longer_fits(instance, phases, day_types):
                db.delete(instance)
                deleted += 1
    db.commit()
    return deleted


def reconcile_family_task_instances(db: Session, family_id: uuid.UUID, today: date | None = None) -> None:
    """Make the future instances of a family's recurring tasks match the templates' phases and the day types.

    The entry point after a template's phases or a school break changed: open, never completed instances that
    no longer fit are deleted, then the rolling window is topped up. Commits.
    """
    today = today or local_today()
    prune_mismatched_instances(db, family_id, today)
    for assignment in _active_assignments(db, family_id):
        generate_instances_for_assignment(db, assignment, today)


def reconcile_family_task_instances_safely(db: Session, family_id: uuid.UUID) -> None:
    """Best effort for the places where the main change was already committed: a failure here must not turn
    that request into an error (the worker repeats the reconcile every few hours)."""
    try:
        reconcile_family_task_instances(db, family_id)
    except Exception:
        logger.warning("Reconciling the task instances of family %s failed", family_id, exc_info=True)
        db.rollback()


def expire_stale_instances(db: Session, family_id: uuid.UUID, today: date | None = None) -> int:
    """Expire missed recurring tasks so they do not pile up ("nicht aufsummieren"). Returns how many.

    Per recurring assignment and phase, the latest instance that is due (due_date <= today) marks the point
    from which older OPEN instances are expired. A missed Wednesday task stays doable on Thursday and Friday and
    expires once Saturday's is due. Templates with ``carry_over`` keep everything. Completed, approved and
    future instances and one-off tasks (no assignment) are never touched. Flushes, the caller commits.
    """
    today = today or local_today()
    latest = (
        select(
            TaskInstance.assignment_id.label("assignment_id"),
            TaskInstance.phase_id.label("phase_id"),
            func.max(TaskInstance.due_date).label("latest_due"),
        )
        .join(TaskAssignment, TaskInstance.assignment_id == TaskAssignment.id)
        .where(TaskAssignment.family_id == family_id, TaskInstance.due_date <= today)
        .group_by(TaskInstance.assignment_id, TaskInstance.phase_id)
        .subquery()
    )
    stale = db.scalars(
        select(TaskInstance)
        .join(TaskTemplate, TaskInstance.template_id == TaskTemplate.id)
        .join(
            latest,
            and_(
                latest.c.assignment_id == TaskInstance.assignment_id,
                TaskInstance.phase_id.is_not_distinct_from(latest.c.phase_id),
            ),
        )
        .where(
            TaskTemplate.carry_over.is_(False),
            TaskInstance.status == TaskStatus.OPEN,
            TaskInstance.due_date < latest.c.latest_due,
        )
    ).all()
    for instance in stale:
        instance.status = TaskStatus.EXPIRED
    db.flush()
    return len(stale)
