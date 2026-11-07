import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_parent
from app.db.session import get_db
from app.models.points import EarningsLedgerEntry
from app.models.user import User, UserRole
from app.schemas.points import EarningsBalance, EarningsLedgerEntryRead

router = APIRouter(prefix="/earnings", tags=["earnings"])


def _forbid_other_child_data(current_user: User, target_id: uuid.UUID) -> None:
    """A child login only sees its own earnings, never a sibling's (parents and the wall dashboard see all)."""
    if current_user.role == UserRole.CHILD and target_id != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nur eigene Daten sichtbar")


@router.get("/ledger", response_model=list[EarningsLedgerEntryRead])
def get_ledger(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    user_id: uuid.UUID | None = None,
) -> list[EarningsLedgerEntry]:
    target_id = user_id or current_user.id
    _forbid_other_child_data(current_user, target_id)

    # If user_id is provided and differs from current_user, verify it belongs to the same family
    if user_id and user_id != current_user.id:
        target_user = db.get(User, user_id)
        if target_user is None or target_user.family_id != current_user.family_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Nutzer nicht gefunden")

    stmt = select(EarningsLedgerEntry).where(EarningsLedgerEntry.user_id == target_id).order_by(
        EarningsLedgerEntry.created_at.desc()
    )
    return list(db.scalars(stmt))


@router.get("/balance", response_model=EarningsBalance)
def get_balance(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    user_id: uuid.UUID | None = None,
) -> EarningsBalance:
    target_id = user_id or current_user.id
    _forbid_other_child_data(current_user, target_id)

    # If user_id is provided and differs from current_user, verify it belongs to the same family
    if user_id and user_id != current_user.id:
        target_user = db.get(User, user_id)
        if target_user is None or target_user.family_id != current_user.family_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Nutzer nicht gefunden")

    def sum_for(status_value: str) -> float:
        return (
            db.scalar(
                select(func.coalesce(func.sum(EarningsLedgerEntry.amount_chf), 0)).where(
                    EarningsLedgerEntry.user_id == target_id,
                    EarningsLedgerEntry.status == status_value,
                )
            )
            or 0.0
        )

    return EarningsBalance(
        user_id=target_id,
        pending_chf=sum_for("pending_approval"),
        approved_chf=sum_for("approved"),
        paid_out_chf=sum_for("paid_out"),
    )


@router.post("/{entry_id}/approve", response_model=EarningsLedgerEntryRead)
def approve_earning(
    entry_id: str,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> EarningsLedgerEntry:
    entry = db.get(EarningsLedgerEntry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eintrag nicht gefunden")

    # Verify the entry's assignee belongs to the current user's family
    assignee = db.get(User, entry.user_id)
    if assignee is None or assignee.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eintrag nicht gefunden")

    entry.status = "approved"
    entry.approved_by = current_user.id
    db.commit()
    db.refresh(entry)
    return entry


@router.post("/{entry_id}/pay-out", response_model=EarningsLedgerEntryRead)
def pay_out_earning(
    entry_id: str,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> EarningsLedgerEntry:
    """Kein echtes Zahlungs-Backend im MVP -- Elternteil markiert manuell als ausgezahlt."""
    entry = db.get(EarningsLedgerEntry, entry_id)
    if entry is None or entry.status != "approved":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kein auszahlbarer Eintrag gefunden")

    # Verify the entry's assignee belongs to the current user's family
    assignee = db.get(User, entry.user_id)
    if assignee is None or assignee.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kein auszahlbarer Eintrag gefunden")

    entry.status = "paid_out"
    db.commit()
    db.refresh(entry)
    return entry
