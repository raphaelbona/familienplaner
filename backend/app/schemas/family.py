import re
import uuid
from datetime import date, datetime

from pydantic import BaseModel, field_validator

from app.models.user import UserRole


class UserRead(BaseModel):
    id: uuid.UUID
    role: UserRole
    display_name: str
    avatar_key: str | None = None

    model_config = {"from_attributes": True}


class PinAccountRead(BaseModel):
    """Entry of the PIN login picker (children and the wall-dashboard user)."""

    id: uuid.UUID
    display_name: str
    avatar_key: str | None = None
    role: UserRole

    model_config = {"from_attributes": True}


class ChildAccountRead(BaseModel):
    id: uuid.UUID
    display_name: str
    avatar_key: str | None = None
    # Position among ALL children of the family (see app/core/person_color.py): one stable person color.
    color_index: int = 0

    model_config = {"from_attributes": True}


class DashboardUserRead(BaseModel):
    id: uuid.UUID
    display_name: str
    avatar_key: str | None = None

    model_config = {"from_attributes": True}


class DashboardUserUpsert(BaseModel):
    display_name: str = "Wand-Dashboard"
    pin: str | None = None
    avatar_key: str | None = "📋"

    @field_validator("pin")
    @classmethod
    def validate_pin(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not re.match(r"^\d{4}$", v):
            raise ValueError("PIN muss aus genau 4 Ziffern bestehen")
        return v


class FamilyRead(BaseModel):
    id: uuid.UUID
    name: str
    hosting_mode: str
    locale: str
    latitude: float | None = None
    longitude: float | None = None
    location_name: str | None = None

    model_config = {"from_attributes": True}


class FamilyLocationUpdate(BaseModel):
    latitude: float
    longitude: float
    location_name: str


class InviteRequest(BaseModel):
    display_name: str
    role: UserRole


class ChildCreate(BaseModel):
    display_name: str
    birth_year: int | None = None
    pin: str
    avatar_key: str | None = None

    @field_validator("pin")
    @classmethod
    def validate_pin(cls, v: str) -> str:
        if not re.match(r"^\d{4}$", v):
            raise ValueError("PIN muss aus genau 4 Ziffern bestehen")
        return v

    @field_validator("birth_year")
    @classmethod
    def validate_birth_year(cls, v: int | None) -> int | None:
        if v is None:
            return v
        current_year = date.today().year
        if v < current_year - 18 or v > current_year:
            raise ValueError("Geburtsjahr muss zwischen 18 Jahren Vergangenheit und heute liegen")
        return v


class ChildUpdate(BaseModel):
    display_name: str | None = None
    birth_year: int | None = None
    avatar_key: str | None = None
    pin: str | None = None

    @field_validator("pin")
    @classmethod
    def validate_pin(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not re.match(r"^\d{4}$", v):
            raise ValueError("PIN muss aus genau 4 Ziffern bestehen")
        return v

    @field_validator("birth_year")
    @classmethod
    def validate_birth_year(cls, v: int | None) -> int | None:
        if v is None:
            return v
        current_year = date.today().year
        if v < current_year - 18 or v > current_year:
            raise ValueError("Geburtsjahr muss zwischen 18 Jahren Vergangenheit und heute liegen")
        return v


class ChildProfileRead(BaseModel):
    id: uuid.UUID
    display_name: str
    birth_year: int | None = None
    avatar_key: str | None = None
    # Only filled for parents (and only while a PIN lock is active); children never see lock info.
    pin_locked_until: datetime | None = None
    # Position among ALL children of the family (see app/core/person_color.py), even for a child login.
    color_index: int = 0

    model_config = {"from_attributes": True}
