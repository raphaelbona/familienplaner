import re
import uuid
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _validate_time(value: str) -> str:
    if not _TIME_RE.match(value):
        raise ValueError("Zeit muss im Format HH:MM sein")
    return value


class TimetableEntryRead(BaseModel):
    """Timetable entry schema for API responses."""

    id: uuid.UUID
    day_of_week: int
    start_time: str
    end_time: str
    subject: str
    room: str | None

    model_config = {"from_attributes": True}


class TimetableEntryCreate(BaseModel):
    """Payload for manually adding a single timetable entry."""

    child_user_id: uuid.UUID
    day_of_week: Annotated[int, Field(ge=0, le=6)]
    start_time: str
    end_time: str
    subject: Annotated[str, Field(min_length=1, max_length=100)]
    room: Annotated[str | None, Field(max_length=50)] = None

    _validate_start_time = field_validator("start_time")(_validate_time)
    _validate_end_time = field_validator("end_time")(_validate_time)


class TimetableSlot(BaseModel):
    """A single day+time slot in the weekly-grid manual entry UI."""

    day_of_week: Annotated[int, Field(ge=0, le=6)]
    start_time: str
    end_time: str

    _validate_start_time = field_validator("start_time")(_validate_time)
    _validate_end_time = field_validator("end_time")(_validate_time)


class TimetableBulkAssign(BaseModel):
    """Assign one subject/room to multiple weekly slots at once (weekly-grid manual entry)."""

    child_user_id: uuid.UUID
    subject: Annotated[str, Field(min_length=1, max_length=100)]
    room: Annotated[str | None, Field(max_length=50)] = None
    slots: Annotated[list[TimetableSlot], Field(min_length=1, max_length=100)]


class TimetableEntryUpdate(BaseModel):
    """Payload for editing a single timetable entry. Omitted fields are left unchanged."""

    day_of_week: Annotated[int, Field(ge=0, le=6)] | None = None
    start_time: str | None = None
    end_time: str | None = None
    subject: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    room: Annotated[str | None, Field(max_length=50)] = None

    @field_validator("start_time", "end_time")
    @classmethod
    def _validate_optional_time(cls, value: str | None) -> str | None:
        return _validate_time(value) if value is not None else value
