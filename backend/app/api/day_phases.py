"""Day phases ("Tagesphasen"): the parts of the day a family splits its tasks into, e.g. "Vor der Schule".

Everyone logged in to a family may read them (the child and the wall dashboard show tasks per phase); only
parents write. A phase belongs to exactly one family; foreign ids are always 404.
"""

import uuid
from datetime import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_parent
from app.core.task_recurrence import reconcile_family_task_instances_safely
from app.db.session import get_db
from app.models.day_phase import DAY_TYPE_BOTH, DAY_TYPE_FREE, DAY_TYPE_SCHOOL, DayPhase
from app.models.task import TaskInstance, TaskStatus
from app.models.user import User
from app.schemas.day_phase import DayPhaseCreate, DayPhaseRead, DayPhaseUpdate, check_times

router = APIRouter(prefix="/day-phases", tags=["day-phases"])

NOT_FOUND = "Tagesphase nicht gefunden"
ALREADY_SEEDED = "Es gibt schon Tagesphasen."

# (name, icon, start, end, day_type) in display order; sort_order is the index.
DEFAULT_PHASES: tuple[tuple[str, str, time, time, str], ...] = (
    ("Vor der Schule", "🌅", time(6, 0), time(8, 0), DAY_TYPE_SCHOOL),
    ("Morgen", "☀️", time(7, 0), time(11, 30), DAY_TYPE_FREE),
    ("Mittag", "🍽️", time(11, 30), time(13, 30), DAY_TYPE_BOTH),
    ("Nachmittag", "🎒", time(13, 30), time(18, 0), DAY_TYPE_BOTH),
    ("Vor dem Zubettgehen", "🌙", time(18, 0), time(21, 30), DAY_TYPE_BOTH),
)


def _get_phase(phase_id: uuid.UUID, current_user: User, db: Session) -> DayPhase:
    phase = db.get(DayPhase, phase_id)
    if phase is None or phase.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return phase


@router.get("", response_model=list[DayPhaseRead])
def list_day_phases(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[DayPhase]:
    return list(
        db.scalars(
            select(DayPhase)
            .where(DayPhase.family_id == current_user.family_id)
            .order_by(DayPhase.start_time, DayPhase.sort_order, DayPhase.name)
        )
    )


@router.post("", response_model=DayPhaseRead, status_code=status.HTTP_201_CREATED)
def create_day_phase(
    payload: DayPhaseCreate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> DayPhase:
    phase = DayPhase(family_id=current_user.family_id, **payload.model_dump())
    db.add(phase)
    db.commit()
    db.refresh(phase)
    return phase


@router.post("/defaults", response_model=list[DayPhaseRead], status_code=status.HTTP_201_CREATED)
def seed_default_day_phases(
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> list[DayPhase]:
    """The standard set, only for a family without any phase yet (so it can never duplicate or overwrite)."""
    if db.scalar(select(DayPhase.id).where(DayPhase.family_id == current_user.family_id).limit(1)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, ALREADY_SEEDED)
    phases = [
        DayPhase(
            family_id=current_user.family_id,
            name=name,
            icon=icon,
            start_time=start,
            end_time=end,
            day_type=day_type,
            sort_order=index,
        )
        for index, (name, icon, start, end, day_type) in enumerate(DEFAULT_PHASES)
    ]
    db.add_all(phases)
    db.commit()
    for phase in phases:
        db.refresh(phase)
    return sorted(phases, key=lambda p: (p.start_time, p.sort_order, p.name))


@router.patch("/{phase_id}", response_model=DayPhaseRead)
def update_day_phase(
    phase_id: uuid.UUID,
    payload: DayPhaseUpdate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> DayPhase:
    phase = _get_phase(phase_id, current_user, db)
    changes = payload.model_dump(exclude_unset=True)
    try:
        check_times(changes.get("start_time", phase.start_time), changes.get("end_time", phase.end_time))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    day_type_changed = "day_type" in changes and changes["day_type"] != phase.day_type
    for field, value in changes.items():
        setattr(phase, field, value)
    db.commit()
    db.refresh(phase)
    if day_type_changed:
        # Which days the phase exists on changed: the future instances follow.
        reconcile_family_task_instances_safely(db, current_user.family_id)
    return phase


@router.delete("/{phase_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_day_phase(
    phase_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Open, not yet done instances of the phase go away; every other instance stays (its phase becomes
    empty via ON DELETE SET NULL) and the template links are removed by ON DELETE CASCADE."""
    phase = _get_phase(phase_id, current_user, db)
    family_id = current_user.family_id
    db.execute(
        delete(TaskInstance).where(
            TaskInstance.phase_id == phase.id,
            TaskInstance.status == TaskStatus.OPEN,
            TaskInstance.completed_at.is_(None),
        )
    )
    db.delete(phase)
    db.commit()
    # A template that lost its last phase continues as a plain daily/weekly task.
    reconcile_family_task_instances_safely(db, family_id)
