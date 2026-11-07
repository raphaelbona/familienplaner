"""School holidays and free days ("Ferien und freie Tage").

Everyone logged in to a family may read them (a child only sees the breaks that apply to it, and never the ids
of siblings); only parents write. Rows derived from Google (source "google") are managed in Google.
"""

import uuid
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_parent
from app.core.school_breaks import (
    MSG_FROM_GOOGLE,
    SOURCE_GOOGLE,
    SOURCE_MANUAL,
    affected_children,
    applies_to_child,
    dedupe,
)
from app.core.task_recurrence import reconcile_family_task_instances_safely
from app.core.timetable_calendar import reconcile_family_timetables
from app.db.session import get_db
from app.models.school_break import SchoolBreak
from app.models.user import User, UserRole
from app.schemas.school_break import SchoolBreakCreate, SchoolBreakRead, SchoolBreakUpdate, check_span

router = APIRouter(prefix="/school-breaks", tags=["school-breaks"])

NOT_FOUND = "Nicht gefunden"
DEFAULT_PAST_DAYS = 7
DEFAULT_FUTURE_DAYS = 120
MAX_WINDOW_DAYS = 400


def _validate_children(child_user_ids: list[uuid.UUID], current_user: User, db: Session) -> list[uuid.UUID]:
    """Every id must be a child of the caller's family (404 otherwise, no hint about foreign ids)."""
    ids = dedupe(child_user_ids)
    if not ids:
        return []
    found = set(
        db.scalars(
            select(User.id).where(
                User.id.in_(ids), User.family_id == current_user.family_id, User.role == UserRole.CHILD
            )
        )
    )
    if found != set(ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kind nicht gefunden")
    return ids


def _get_break(break_id: uuid.UUID, current_user: User, db: Session) -> SchoolBreak:
    brk = db.get(SchoolBreak, break_id)
    if brk is None or brk.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return brk


def _manual_break(break_id: uuid.UUID, current_user: User, db: Session) -> SchoolBreak:
    brk = _get_break(break_id, current_user, db)
    if brk.source == SOURCE_GOOGLE:
        raise HTTPException(status.HTTP_409_CONFLICT, MSG_FROM_GOOGLE)
    return brk


@router.get("", response_model=list[SchoolBreakRead])
def list_school_breaks(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
) -> list[SchoolBreakRead]:
    """Breaks overlapping from..to (default: 7 days back to 120 days ahead, at most 400 days)."""
    today = date.today()
    first = from_ or today - timedelta(days=DEFAULT_PAST_DAYS)
    last = to or first + timedelta(days=DEFAULT_PAST_DAYS + DEFAULT_FUTURE_DAYS)
    if last < first:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Das Ende darf nicht vor dem Beginn liegen.")
    last = min(last, first + timedelta(days=MAX_WINDOW_DAYS - 1))

    rows = db.scalars(
        select(SchoolBreak)
        .where(
            SchoolBreak.family_id == current_user.family_id,
            SchoolBreak.end_date >= first,
            SchoolBreak.start_date <= last,
        )
        .order_by(SchoolBreak.start_date, SchoolBreak.end_date, SchoolBreak.title)
    )
    result: list[SchoolBreakRead] = []
    for brk in rows:
        read = SchoolBreakRead.model_validate(brk)
        if current_user.role == UserRole.CHILD:
            if not applies_to_child(brk, current_user.id):
                continue
            # Never leak siblings: "all" stays [], everything else is reduced to the child's own id.
            read.child_user_ids = [current_user.id] if brk.child_user_ids else []
        result.append(read)
    return result


@router.post("", response_model=SchoolBreakRead, status_code=status.HTTP_201_CREATED)
def create_school_break(
    payload: SchoolBreakCreate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> SchoolBreak:
    children = _validate_children(payload.child_user_ids, current_user, db)
    brk = SchoolBreak(
        family_id=current_user.family_id,
        title=payload.title,
        kind=payload.kind,
        start_date=payload.start_date,
        end_date=payload.end_date,
        child_user_ids=children,
        source=SOURCE_MANUAL,
    )
    db.add(brk)
    db.commit()
    db.refresh(brk)
    reconcile_family_timetables(db, current_user.family_id, affected_children(children))
    reconcile_family_task_instances_safely(db, current_user.family_id)
    return brk


@router.patch("/{break_id}", response_model=SchoolBreakRead)
def update_school_break(
    break_id: uuid.UUID,
    payload: SchoolBreakUpdate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> SchoolBreak:
    brk = _manual_break(break_id, current_user, db)
    changes = payload.model_dump(exclude_unset=True)
    old_children = list(brk.child_user_ids)

    if "child_user_ids" in changes:
        changes["child_user_ids"] = _validate_children(changes["child_user_ids"], current_user, db)
    try:
        check_span(changes.get("start_date", brk.start_date), changes.get("end_date", brk.end_date))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    for field, value in changes.items():
        setattr(brk, field, value)
    db.commit()
    db.refresh(brk)
    reconcile_family_timetables(
        db, current_user.family_id, affected_children(old_children, list(brk.child_user_ids))
    )
    reconcile_family_task_instances_safely(db, current_user.family_id)
    return brk


@router.delete("/{break_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_school_break(
    break_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    brk = _manual_break(break_id, current_user, db)
    children = list(brk.child_user_ids)
    db.delete(brk)
    db.commit()
    reconcile_family_timetables(db, current_user.family_id, affected_children(children))
    reconcile_family_task_instances_safely(db, current_user.family_id)
