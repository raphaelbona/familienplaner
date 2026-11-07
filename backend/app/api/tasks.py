import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_parent
from app.core.clock import local_now
from app.core.config import get_settings
from app.core.day_type import day_type_for, phase_applies
from app.core.push import send_push_to_user
from app.core.task_recurrence import (
    expire_stale_instances,
    generate_instances_for_assignment,
    reconcile_family_task_instances_safely,
)
from app.db.session import get_db
from app.models.day_phase import DayPhase
from app.models.points import EarningsLedgerEntry, PointsLedgerEntry
from app.models.task import TaskAssignment, TaskInstance, TaskKind, TaskStatus, TaskTemplate
from app.models.user import User, UserRole
from app.schemas.task import (
    TaskInstanceCreate,
    TaskInstanceRead,
    TaskTemplateCreate,
    TaskTemplateRead,
    TaskTemplateUpdate,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])

PHASE_NOT_FOUND = "Tagesphase nicht gefunden"
MSG_EXPIRED = "Aufgabe ist verfallen."


def _family_phases(db: Session, current_user: User, phase_ids: list[uuid.UUID]) -> list[DayPhase]:
    """The phases with these ids, all of the caller's family (404 otherwise, no hint about foreign ids)."""
    if not phase_ids:
        return []
    found = {
        phase.id: phase
        for phase in db.scalars(
            select(DayPhase).where(DayPhase.id.in_(phase_ids), DayPhase.family_id == current_user.family_id)
        )
    }
    if len(found) != len(set(phase_ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, PHASE_NOT_FOUND)
    return [found[phase_id] for phase_id in dict.fromkeys(phase_ids)]


@router.post("/templates", response_model=TaskTemplateRead, status_code=status.HTTP_201_CREATED)
def create_template(
    payload: TaskTemplateCreate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> TaskTemplate:
    phases = _family_phases(db, current_user, payload.phase_ids)
    template = TaskTemplate(family_id=current_user.family_id, **payload.model_dump(exclude={"phase_ids"}))
    template.phases = phases
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.get("/templates", response_model=list[TaskTemplateRead])
def list_templates(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[TaskTemplate]:
    stmt = select(TaskTemplate).where(
        TaskTemplate.family_id == current_user.family_id, TaskTemplate.archived_at.is_(None)
    )
    return list(db.scalars(stmt))


TEMPLATE_NOT_FOUND = "Vorlage nicht gefunden"
ECONOMIC_FIELDS = ("kind", "points_value", "bonus_multiplier", "money_value_chf")


def _get_active_template(db: Session, current_user: User, template_id: uuid.UUID) -> TaskTemplate:
    """A non-archived template of the caller's family, else 404 (never reveals other families' templates)."""
    template = db.get(TaskTemplate, template_id)
    if template is None or template.family_id != current_user.family_id or template.archived_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, TEMPLATE_NOT_FOUND)
    return template


@router.patch("/templates/{template_id}", response_model=TaskTemplateRead)
def update_template(
    template_id: uuid.UUID,
    payload: TaskTemplateUpdate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> TaskTemplate:
    """Edit a template. Title/icon always; kind/points/bonus/money only while nothing was approved yet,
    because approving books points/money in the ledgers."""
    template = _get_active_template(db, current_user, template_id)

    merged = {name: getattr(template, name) for name in TaskTemplateCreate.model_fields}
    merged.update(payload.model_dump(exclude_unset=True))
    try:
        validated = TaskTemplateCreate(**merged)  # same rules as create
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, errors) from exc

    economics_changed = any(getattr(template, name) != getattr(validated, name) for name in ECONOMIC_FIELDS)
    if economics_changed:
        approved = db.scalar(
            select(TaskInstance.id)
            .where(TaskInstance.template_id == template.id, TaskInstance.status == TaskStatus.APPROVED)
            .limit(1)
        )
        if approved is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Art und Punkte lassen sich nicht mehr ändern, weil schon Aufgaben bestätigt wurden.",
            )

    phases = _family_phases(db, current_user, validated.phase_ids)
    phases_changed = {p.id for p in phases} != {p.id for p in template.phases}

    for name, value in validated.model_dump(exclude={"phase_ids"}).items():
        setattr(template, name, value)
    if phases_changed:
        template.phases = phases
    db.commit()
    db.refresh(template)
    if phases_changed:
        # The future instances follow the new phases (one per phase and day).
        reconcile_family_task_instances_safely(db, current_user.family_id)
        db.refresh(template)
    return template


@router.delete("/templates/{template_id}")
def delete_template(
    template_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    """Hard delete when nothing references the template, otherwise archive it so history/ledgers stay intact."""
    template = _get_active_template(db, current_user, template_id)

    referenced = (
        db.scalar(select(TaskInstance.id).where(TaskInstance.template_id == template.id).limit(1)) is not None
        or db.scalar(select(TaskAssignment.id).where(TaskAssignment.template_id == template.id).limit(1)) is not None
    )
    if referenced:
        template.archived_at = datetime.now(UTC)
        db.commit()
        return {"result": "archived"}
    db.delete(template)
    db.commit()
    return {"result": "deleted"}


@router.post("/instances", response_model=TaskInstanceRead, status_code=status.HTTP_201_CREATED)
def create_instance(
    payload: TaskInstanceCreate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> TaskInstance:
    assignee_ids = payload.assignee_user_ids if payload.assignee_user_ids else [payload.assignee_user_id]

    # Verify every assignee belongs to current_user's family
    for assignee_id in assignee_ids:
        assignee = db.get(User, assignee_id)
        if assignee is None or assignee.family_id != current_user.family_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Familienmitglied oder Aufgaben-Vorlage nicht gefunden")

    # Verify template_id belongs to current_user's family
    template = db.get(TaskTemplate, payload.template_id)
    if template is None or template.family_id != current_user.family_id or template.archived_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Familienmitglied oder Aufgaben-Vorlage nicht gefunden")

    weekdays_value = ",".join(str(d) for d in payload.weekdays) if payload.weekdays else None

    phases = list(template.phases)

    created_instances: list[TaskInstance] = []
    for assignee_id in assignee_ids:
        if payload.recurrence_rule is None:
            # One checkable instance per phase that exists on that day for the assignee, else a plain one.
            phase_ids: list[uuid.UUID | None] = [None]
            if phases:
                day_type = day_type_for(db, assignee_id, current_user.family_id, payload.due_date)
                phase_ids = [p.id for p in phases if phase_applies(p, day_type)] or [None]
            new_instances = [
                TaskInstance(
                    template_id=payload.template_id,
                    assignee_user_id=assignee_id,
                    due_date=payload.due_date,
                    phase_id=phase_id,
                )
                for phase_id in phase_ids
            ]
            db.add_all(new_instances)
            db.commit()
            for new_instance in new_instances:
                db.refresh(new_instance)
            instance = new_instances[0]
        else:
            assignment = TaskAssignment(
                family_id=current_user.family_id,
                template_id=payload.template_id,
                assignee_user_id=assignee_id,
                recurrence_rule=payload.recurrence_rule,
                weekdays=weekdays_value,
                start_date=payload.due_date,
            )
            db.add(assignment)
            db.commit()
            db.refresh(assignment)

            created = generate_instances_for_assignment(db, assignment, today=local_now().date())
            if not created:
                # Only possible with phases: none of them exists on any due day of the coming weeks.
                db.delete(assignment)
                db.commit()
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Mit den Tagesphasen dieser Aufgabe gibt es in den nächsten Wochen keinen passenden Tag.",
                )
            instance = created[0]

        send_push_to_user(
            db, assignee_id, "Neue Aufgabe", f"{template.title} ist jetzt für dich fällig.", get_settings()
        )
        created_instances.append(instance)

    return created_instances[0]


@router.get("/instances", response_model=list[TaskInstanceRead])
def list_instances(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    assignee_user_id: uuid.UUID | None = None,
    status_filter: TaskStatus | None = None,
    due_after: date | None = None,
    due_before: date | None = None,
) -> list[TaskInstance]:
    # Missed recurring tasks expire when the next one is due (best effort: listing must not fail because of it).
    try:
        expire_stale_instances(db, current_user.family_id, today=local_now().date())
        db.commit()
    except SQLAlchemyError:
        db.rollback()

    # A child login only sees its own task instances, never a sibling's.
    if current_user.role == UserRole.CHILD:
        if assignee_user_id is not None and assignee_user_id != current_user.id:
            return []
        assignee_user_id = current_user.id

    # If assignee_user_id is provided, verify it belongs to current_user's family
    if assignee_user_id:
        assignee = db.get(User, assignee_user_id)
        if assignee is None or assignee.family_id != current_user.family_id:
            # Return empty list instead of raising, matching no-results case
            return []

    # Always scope by family: only return instances whose assignee is in current_user's family
    # Explicitly specify the join condition to avoid ambiguity (TaskInstance has two FK to User)
    stmt = select(TaskInstance).join(
        User, TaskInstance.assignee_user_id == User.id
    ).where(User.family_id == current_user.family_id)
    if assignee_user_id:
        stmt = stmt.where(TaskInstance.assignee_user_id == assignee_user_id)
    if status_filter:
        stmt = stmt.where(TaskInstance.status == status_filter)
    if due_after:
        stmt = stmt.where(TaskInstance.due_date >= due_after)
    if due_before:
        stmt = stmt.where(TaskInstance.due_date <= due_before)
    return list(db.scalars(stmt.order_by(TaskInstance.due_date)))


def _get_own_or_family_instance(db: Session, current_user: User, instance_id: uuid.UUID) -> TaskInstance:
    """Instance the caller may mark done / undo: their own, or (as parent or wall dashboard) one of their family."""
    instance = db.get(TaskInstance, instance_id)
    if instance is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aufgabe nicht gefunden")

    # A child marks their own task done. A parent or dashboard user may also do it
    # on a shared wall tablet, tapping a child's task on their behalf.
    is_own_task = instance.assignee_user_id == current_user.id
    can_act_for_family = False
    if not is_own_task and current_user.role.value in ("parent_admin", "parent_member", "dashboard"):
        assignee = db.get(User, instance.assignee_user_id)
        can_act_for_family = assignee is not None and assignee.family_id == current_user.family_id

    if not (is_own_task or can_act_for_family):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aufgabe nicht gefunden")
    return instance


@router.post("/instances/{instance_id}/complete", response_model=TaskInstanceRead)
def complete_instance(
    instance_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TaskInstance:
    instance = _get_own_or_family_instance(db, current_user, instance_id)
    if instance.status == TaskStatus.EXPIRED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, MSG_EXPIRED)

    now = local_now()
    if instance.due_date > now.date():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aufgabe ist erst am Fälligkeitstag abhakbar.")

    if instance.status != TaskStatus.OPEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aufgabe wurde bereits erledigt gemeldet oder freigegeben.")

    # A phase task is doable from the start of its phase on (past days are never gated).
    if instance.phase_id is not None and instance.due_date == now.date():
        phase = db.get(DayPhase, instance.phase_id)
        if phase is not None and now.time() < phase.start_time:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Diese Phase beginnt erst um {phase.start_time:%H:%M}."
            )
    instance.status = TaskStatus.COMPLETED
    instance.completed_at = datetime.now(UTC)
    db.commit()
    db.refresh(instance)
    return instance


@router.post("/instances/{instance_id}/reopen", response_model=TaskInstanceRead)
def reopen_instance(
    instance_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TaskInstance:
    """Undo 'ich bin fertig' (COMPLETED -> OPEN). Impossible once a parent approved: approval books the ledgers."""
    instance = _get_own_or_family_instance(db, current_user, instance_id)

    if instance.status == TaskStatus.EXPIRED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, MSG_EXPIRED)
    if instance.status == TaskStatus.APPROVED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aufgabe wurde bereits von den Eltern bestätigt.")
    if instance.status != TaskStatus.COMPLETED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aufgabe ist nicht als erledigt markiert.")
    instance.status = TaskStatus.OPEN
    instance.completed_at = None
    db.commit()
    db.refresh(instance)
    return instance


@router.post("/instances/{instance_id}/approve", response_model=TaskInstanceRead)
def approve_instance(
    instance_id: str,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> TaskInstance:
    """Elternteil bestätigt eine erledigte Aufgabe -> schreibt Punkte-/Geld-Ledger-Eintrag."""
    instance = db.get(TaskInstance, instance_id)
    if instance is None or instance.status not in (TaskStatus.COMPLETED, TaskStatus.EXPIRED):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Keine bestätigungsfähige Aufgabe gefunden")

    # Verify the instance's assignee belongs to the current user's family
    assignee = db.get(User, instance.assignee_user_id)
    if assignee is None or assignee.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Keine bestätigungsfähige Aufgabe gefunden")
    if instance.status == TaskStatus.EXPIRED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, MSG_EXPIRED)

    template = db.get(TaskTemplate, instance.template_id)
    instance.status = TaskStatus.APPROVED
    instance.approved_by = current_user.id

    if template and template.kind != TaskKind.BASIS and template.points_value:
        effective_points = template.points_value
        if template.kind == TaskKind.BONUS:
            effective_points = round(template.points_value * template.bonus_multiplier)
        db.add(
            PointsLedgerEntry(
                user_id=instance.assignee_user_id,
                delta=effective_points,
                reason="task_completed",
                task_instance_id=instance.id,
            )
        )
    if template and template.kind == TaskKind.GELD and template.money_value_chf:
        db.add(
            EarningsLedgerEntry(
                user_id=instance.assignee_user_id,
                amount_chf=template.money_value_chf,
                status="approved",
                task_instance_id=instance.id,
                approved_by=current_user.id,
            )
        )

    db.commit()
    db.refresh(instance)
    return instance
