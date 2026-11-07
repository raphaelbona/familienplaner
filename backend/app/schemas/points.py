import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class PointsLedgerEntryRead(BaseModel):
    id: uuid.UUID
    delta: int
    reason: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PointsBalance(BaseModel):
    user_id: uuid.UUID
    balance: int


class RewardCreate(BaseModel):
    title: str
    points_cost: int = Field(gt=0)
    icon: str | None = None


class RewardRead(RewardCreate):
    id: uuid.UUID

    model_config = {"from_attributes": True}


class EarningsLedgerEntryRead(BaseModel):
    id: uuid.UUID
    amount_chf: float
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class EarningsBalance(BaseModel):
    user_id: uuid.UUID
    pending_chf: float
    approved_chf: float
    paid_out_chf: float
