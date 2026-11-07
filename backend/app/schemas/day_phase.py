import uuid
from datetime import time
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

DayType = Literal["school", "free", "both"]
Name = Annotated[str, Field(min_length=1, max_length=40)]
Icon = Annotated[str, Field(max_length=8)]
SortOrder = Annotated[int, Field(ge=0, le=1000)]

MSG_TIMES = "Das Ende muss nach dem Beginn liegen."


def check_times(start: time, end: time) -> None:
    if end <= start:
        raise ValueError(MSG_TIMES)


def _naive_time(value: time | None) -> time | None:
    if value is not None and value.tzinfo is not None:
        raise ValueError("Bitte die Uhrzeit ohne Zeitzone angeben.")
    return value


def _clean_name(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("Der Name darf nicht leer sein.")
    return cleaned


def _clean_icon(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


class DayPhaseRead(BaseModel):
    id: uuid.UUID
    name: str
    icon: str | None
    start_time: time
    end_time: time
    day_type: DayType
    sort_order: int

    model_config = {"from_attributes": True}


class DayPhaseCreate(BaseModel):
    name: Name
    icon: Icon | None = None
    start_time: time
    end_time: time
    day_type: DayType
    sort_order: SortOrder = 0

    _clean_name = field_validator("name")(_clean_name)
    _clean_icon = field_validator("icon")(_clean_icon)
    _naive = field_validator("start_time", "end_time")(_naive_time)

    @model_validator(mode="after")
    def _times(self) -> "DayPhaseCreate":
        check_times(self.start_time, self.end_time)
        return self


class DayPhaseUpdate(BaseModel):
    """Omitted fields stay unchanged (``icon: null`` clears the icon)."""

    name: Name | None = None
    icon: Icon | None = None
    start_time: time | None = None
    end_time: time | None = None
    day_type: DayType | None = None
    sort_order: SortOrder | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return _clean_name(value) if value is not None else None

    _clean_icon = field_validator("icon")(_clean_icon)
    _naive = field_validator("start_time", "end_time")(_naive_time)

    @model_validator(mode="after")
    def _check(self) -> "DayPhaseUpdate":
        for field in ("name", "start_time", "end_time", "day_type", "sort_order"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} darf nicht leer sein.")
        if self.start_time is not None and self.end_time is not None:
            check_times(self.start_time, self.end_time)
        return self
