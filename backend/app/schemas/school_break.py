import uuid
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.school_breaks import MAX_SPAN_DAYS

Kind = Literal["ferien", "schulfrei"]
Title = Annotated[str, Field(min_length=1, max_length=100)]
ChildIds = Annotated[list[uuid.UUID], Field(max_length=50)]


def check_span(start: date, end: date) -> None:
    if end < start:
        raise ValueError("Das Ende darf nicht vor dem Beginn liegen.")
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise ValueError(f"Ein Eintrag darf höchstens {MAX_SPAN_DAYS} Tage dauern.")


def _clean_title(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("Der Name darf nicht leer sein.")
    return cleaned


class SchoolBreakRead(BaseModel):
    id: uuid.UUID
    title: str
    kind: str
    start_date: date
    end_date: date
    child_user_ids: list[uuid.UUID]
    source: str

    model_config = {"from_attributes": True}


class SchoolBreakCreate(BaseModel):
    title: Title
    kind: Kind = "ferien"
    start_date: date
    end_date: date
    child_user_ids: ChildIds = Field(default_factory=list)

    _clean_title = field_validator("title")(_clean_title)

    @field_validator("child_user_ids")
    @classmethod
    def _unique_children(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _dates(self) -> "SchoolBreakCreate":
        check_span(self.start_date, self.end_date)
        return self


class SchoolBreakUpdate(BaseModel):
    """Omitted fields stay unchanged."""

    title: Title | None = None
    kind: Kind | None = None
    start_date: date | None = None
    end_date: date | None = None
    child_user_ids: ChildIds | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        return _clean_title(value) if value is not None else None

    @field_validator("child_user_ids")
    @classmethod
    def _unique_children(cls, value: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
        return list(dict.fromkeys(value)) if value is not None else None

    @model_validator(mode="after")
    def _dates(self) -> "SchoolBreakUpdate":
        for field in ("title", "kind", "start_date", "end_date", "child_user_ids"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} darf nicht leer sein.")
        if self.start_date is not None and self.end_date is not None:
            check_span(self.start_date, self.end_date)
        return self
