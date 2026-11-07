import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class DeviceCreate(BaseModel):
    label: str = Field(min_length=1, max_length=60)

    @field_validator("label")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Bezeichnung darf nicht leer sein")
        return value


class DevicePaired(BaseModel):
    id: uuid.UUID
    label: str
    device_token: str
    expires_at: datetime


class DeviceRead(BaseModel):
    id: uuid.UUID
    label: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    revoked: bool
