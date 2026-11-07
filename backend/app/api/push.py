from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.schemas.push import PushSubscriptionCreate, VapidPublicKeyResponse

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/vapid-public-key", response_model=VapidPublicKeyResponse)
def get_vapid_public_key() -> VapidPublicKeyResponse:
    """Returns null when push isn't configured; the frontend then skips offering it."""
    return VapidPublicKeyResponse(public_key=get_settings().vapid_public_key)


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
def subscribe(
    payload: PushSubscriptionCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    existing = db.scalar(
        select(PushSubscription).where(
            PushSubscription.user_id == current_user.id, PushSubscription.endpoint == payload.endpoint
        )
    )
    if existing:
        existing.p256dh = payload.p256dh
        existing.auth = payload.auth
    else:
        db.add(PushSubscription(user_id=current_user.id, **payload.model_dump()))
    db.commit()
    return {"detail": "Abonniert."}


@router.delete("/subscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(
    endpoint: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    db.execute(
        delete(PushSubscription).where(
            PushSubscription.user_id == current_user.id, PushSubscription.endpoint == endpoint
        )
    )
    db.commit()
