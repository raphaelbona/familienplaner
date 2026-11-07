"""Shared helpers for the day-phase / task-expiry tests: phases, timetables, templates, assignments, instances
and a frozen clock (Monday 2026-11-02, 09:00 local time)."""

import uuid
from datetime import date, datetime, time, timedelta

from app.core.clock import local_timezone
from app.models.day_phase import DayPhase
from app.models.task import TaskAssignment, TaskInstance, TaskKind, TaskStatus, TaskTemplate
from app.models.timetable import TimetableEntry

MON = date(2026, 11, 2)  # a Monday
FROZEN_NOW = datetime(2026, 11, 2, 9, 0, tzinfo=local_timezone())


def d(offset: int) -> date:
    """Date relative to the frozen Monday."""
    return MON + timedelta(days=offset)


def freeze_clock(monkeypatch, now: datetime = FROZEN_NOW) -> None:
    """Pin the family-local clock of the task logic (API and core defaults)."""
    monkeypatch.setattr("app.api.tasks.local_now", lambda: now)
    monkeypatch.setattr("app.core.task_recurrence.local_today", lambda: now.date())


def add_phase(
    db, family, name="Mittag", start=(11, 30), end=(13, 30), day_type="both", sort_order=0, icon=None
) -> DayPhase:
    phase = DayPhase(
        family_id=family.id,
        name=name,
        icon=icon,
        start_time=time(*start),
        end_time=time(*end),
        day_type=day_type,
        sort_order=sort_order,
    )
    db.add(phase)
    db.commit()
    return phase


def add_timetable(db, family, child, weekdays=(0, 1, 2, 3, 4)) -> None:
    """One lesson on each of the given weekdays (0=Mon..6=Sun): those are the child's school days."""
    for weekday in weekdays:
        db.add(
            TimetableEntry(
                family_id=family.id, child_user_id=child.id, day_of_week=weekday,
                start_time="08:00", end_time="09:00", subject="Mathe", room=None,
            )
        )
    db.commit()


def add_template(db, family, *, phases=(), carry_over=False, title="Duschen", kind=TaskKind.BASIS) -> TaskTemplate:
    template = TaskTemplate(family_id=family.id, title=title, kind=kind, carry_over=carry_over)
    template.phases = list(phases)
    db.add(template)
    db.commit()
    return template


def add_assignment(db, family, child, template, rule="daily", weekdays=None, start=MON) -> TaskAssignment:
    assignment = TaskAssignment(
        family_id=family.id,
        template_id=template.id,
        assignee_user_id=child.id,
        recurrence_rule=rule,
        weekdays=weekdays,
        start_date=start,
    )
    db.add(assignment)
    db.commit()
    return assignment


def add_instance(
    db, template, child, due, *, assignment=None, status=TaskStatus.OPEN, phase=None
) -> TaskInstance:
    instance = TaskInstance(
        template_id=template.id,
        assignee_user_id=child.id,
        assignment_id=assignment.id if assignment else None,
        due_date=due,
        status=status,
        phase_id=phase.id if phase else None,
    )
    db.add(instance)
    db.commit()
    return instance


def instances_of(db, assignment) -> list[TaskInstance]:
    from sqlalchemy import select

    db.expire_all()
    return list(
        db.scalars(
            select(TaskInstance)
            .where(TaskInstance.assignment_id == assignment.id)
            .order_by(TaskInstance.due_date, TaskInstance.phase_id)
        )
    )


def by_day(instances, *, status=None) -> dict[date, set[uuid.UUID | None]]:
    """{due_date: {phase_id, ...}} of the instances (optionally only those with this status)."""
    result: dict[date, set[uuid.UUID | None]] = {}
    for instance in instances:
        if status is None or instance.status == status:
            result.setdefault(instance.due_date, set()).add(instance.phase_id)
    return result
