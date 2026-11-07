import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.task import TaskKind, TaskStatus

PhaseIds = Annotated[list[uuid.UUID], Field(max_length=20)]


def _unique_ids(value: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
    return list(dict.fromkeys(value)) if value is not None else None


class TaskTemplateCreate(BaseModel):
    title: str
    kind: TaskKind
    points_value: int = 0
    bonus_multiplier: float = 1.0
    money_value_chf: float = 0.0
    recurrence_rule: str | None = None
    assignee_role: str = "any"
    icon: str | None = None
    # Day phases the task is split into (one checkable instance per phase and day); all must be the family's.
    phase_ids: PhaseIds = []
    # False: a missed recurring task expires when the next one is due. True: missed ones stay doable.
    carry_over: bool = False

    _unique_phase_ids = field_validator("phase_ids")(_unique_ids)

    @model_validator(mode="after")
    def validate_bonus_multiplier(self):
        if self.bonus_multiplier != 1.0 and self.kind != TaskKind.BONUS:
            raise ValueError("bonus_multiplier ist nur für Bonusaufgaben (kind='bonus') erlaubt")
        if self.bonus_multiplier <= 0 or self.bonus_multiplier > 5:
            raise ValueError(f"bonus_multiplier muss zwischen 0 und 5 liegen (war {self.bonus_multiplier})")
        if self.kind == TaskKind.BASIS and self.points_value != 0:
            raise ValueError("Basis-Aufgaben sind Pflicht und vergeben keine Punkte (points_value muss 0 sein)")
        return self


class TaskTemplateUpdate(BaseModel):
    """Partial update: only fields that are sent are changed (``icon: null`` clears the icon).

    The merged result is re-validated with TaskTemplateCreate in the API, so the create rules apply.
    """

    title: str | None = Field(default=None, max_length=120)
    kind: TaskKind | None = None
    points_value: int | None = Field(default=None, ge=0)
    bonus_multiplier: float | None = None
    money_value_chf: float | None = Field(default=None, ge=0)
    recurrence_rule: str | None = None
    assignee_role: str | None = Field(default=None, max_length=20)
    icon: str | None = Field(default=None, max_length=8)
    phase_ids: PhaseIds | None = None
    carry_over: bool | None = None

    _unique_phase_ids = field_validator("phase_ids")(_unique_ids)

    @model_validator(mode="after")
    def reject_null_for_required_fields(self):
        for name in self.model_fields_set - {"icon", "recurrence_rule"}:
            if getattr(self, name) is None:
                raise ValueError(f"{name} darf nicht null sein")
        return self

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Titel darf nicht leer sein")
        return value


class TaskTemplateRead(TaskTemplateCreate):
    id: uuid.UUID

    model_config = {"from_attributes": True}


class TaskInstanceCreate(BaseModel):
    template_id: uuid.UUID
    assignee_user_id: uuid.UUID | None = None
    assignee_user_ids: list[uuid.UUID] | None = None
    due_date: date
    recurrence_rule: Literal["daily", "weekly"] | None = None
    # Which weekdays (0=Mon..6=Sun) a "weekly" task is due on, e.g. [0, 3] for
    # "zweimal die Woche" (Mon+Thu). Omit for the legacy once-per-week behavior.
    weekdays: list[int] | None = None

    @model_validator(mode="after")
    def validate_assignees_and_weekdays(self):
        if not self.assignee_user_id and not self.assignee_user_ids:
            raise ValueError("assignee_user_id oder assignee_user_ids ist erforderlich")
        if self.weekdays is not None:
            if self.recurrence_rule != "weekly":
                raise ValueError("weekdays ist nur bei recurrence_rule='weekly' erlaubt")
            if not self.weekdays or any(d < 0 or d > 6 for d in self.weekdays):
                raise ValueError("weekdays muss 1-7 Werte zwischen 0 (Montag) und 6 (Sonntag) enthalten")
        return self


class TaskInstanceRead(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    assignee_user_id: uuid.UUID
    due_date: date
    phase_id: uuid.UUID | None = None
    status: TaskStatus
    completed_at: datetime | None
    approved_by: uuid.UUID | None
    template: TaskTemplateRead

    model_config = {"from_attributes": True}
