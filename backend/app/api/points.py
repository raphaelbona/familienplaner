from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_parent
from app.db.session import get_db
from app.models.points import PointsLedgerEntry, RewardCatalogItem
from app.models.user import User
from app.schemas.points import PointsBalance, PointsLedgerEntryRead, RewardCreate, RewardRead

router = APIRouter(tags=["points"])


@router.get("/rewards", response_model=list[RewardRead])
def list_rewards(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[RewardCatalogItem]:
    stmt = select(RewardCatalogItem).where(
        RewardCatalogItem.family_id == current_user.family_id
    ).order_by(RewardCatalogItem.points_cost)
    return list(db.scalars(stmt))


def _resolve_target_user_id(user_id: str | None, current_user: User, db: Session) -> str:
    """Resolve the effective target user id, enforcing same-family access for a foreign id."""
    if not user_id or user_id == str(current_user.id):
        return str(current_user.id)

    target_user = db.get(User, user_id)
    if target_user is None or target_user.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nutzer nicht gefunden")
    return user_id


@router.get("/points/ledger", response_model=list[PointsLedgerEntryRead])
def get_ledger(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    user_id: str | None = None,
) -> list[PointsLedgerEntry]:
    target_id = _resolve_target_user_id(user_id, current_user, db)
    stmt = select(PointsLedgerEntry).where(PointsLedgerEntry.user_id == target_id).order_by(
        PointsLedgerEntry.created_at.desc()
    )
    return list(db.scalars(stmt))


@router.get("/points/balance", response_model=PointsBalance)
def get_balance(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    user_id: str | None = None,
) -> PointsBalance:
    target_id = _resolve_target_user_id(user_id, current_user, db)
    total = db.scalar(
        select(func.coalesce(func.sum(PointsLedgerEntry.delta), 0)).where(PointsLedgerEntry.user_id == target_id)
    )
    return PointsBalance(user_id=target_id, balance=total or 0)


@router.post("/rewards", response_model=RewardRead, status_code=status.HTTP_201_CREATED)
def create_reward(
    payload: RewardCreate,
    current_user: Annotated[User, Depends(require_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> RewardCatalogItem:
    reward = RewardCatalogItem(family_id=current_user.family_id, **payload.model_dump())
    db.add(reward)
    db.commit()
    db.refresh(reward)
    return reward


@router.post("/rewards/{reward_id}/redeem")
def redeem_reward(
    reward_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    reward = db.get(RewardCatalogItem, reward_id)
    if reward is None or reward.family_id != current_user.family_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Belohnung nicht gefunden")

    total = db.scalar(
        select(func.coalesce(func.sum(PointsLedgerEntry.delta), 0)).where(
            PointsLedgerEntry.user_id == current_user.id
        )
    ) or 0
    if total < reward.points_cost:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nicht genug Punkte")

    db.add(
        PointsLedgerEntry(
            user_id=current_user.id,
            delta=-reward.points_cost,
            reason="reward_redeemed",
        )
    )
    db.commit()
    return {"redeemed": reward.title, "remaining_balance": total - reward.points_cost}
