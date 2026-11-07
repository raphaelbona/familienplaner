import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Table, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.day_phase import DayPhase
from app.models.mixins import TimestampMixin, UUIDPk


class TaskKind(str, enum.Enum):
    BASIS = "basis"
    BONUS = "bonus"
    GELD = "geld"


class TaskStatus(str, enum.Enum):
    OPEN = "open"
    COMPLETED = "completed"
    APPROVED = "approved"
    # An open recurring task nobody did before the next one became due (see app.core.task_recurrence).
    EXPIRED = "expired"


# Which day phases a template is split into: one checkable instance per phase and day.
task_template_phase = Table(
    "task_template_phase",
    Base.metadata,
    Column("template_id", UUID(as_uuid=True), ForeignKey("task_template.id", ondelete="CASCADE"), primary_key=True),
    Column("phase_id", UUID(as_uuid=True), ForeignKey("day_phase.id", ondelete="CASCADE"), primary_key=True),
)


class TaskTemplate(Base, UUIDPk, TimestampMixin):
    __tablename__ = "task_template"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(150))
    kind: Mapped[TaskKind] = mapped_column(Enum(TaskKind, name="task_kind"))
    points_value: Mapped[int] = mapped_column(Integer, default=0)
    bonus_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    money_value_chf: Mapped[float] = mapped_column(Float, default=0.0)
    recurrence_rule: Mapped[str | None] = mapped_column(String(200), nullable=True)
    assignee_role: Mapped[str] = mapped_column(String(20), default="any")  # "any" | user id as string
    icon: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Set instead of deleting when instances/assignments still reference the template: hidden from lists,
    # no new tasks can be created from it, but history and ledgers keep their template info.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # False: a missed recurring task expires once the next one is due ("nicht aufsummieren"); True: it stays.
    carry_over: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    phases: Mapped[list[DayPhase]] = relationship(
        secondary=task_template_phase,
        order_by=lambda: (DayPhase.start_time, DayPhase.sort_order, DayPhase.name),
        lazy="selectin",
    )

    @property
    def phase_ids(self) -> list[uuid.UUID]:
        return [phase.id for phase in self.phases]


class TaskAssignment(Base, UUIDPk, TimestampMixin):
    """A recurring assignment of a task template to one child (daily or weekly).

    Generates a rolling window of TaskInstance rows (see app.core.task_recurrence)
    instead of the parent having to re-assign the same task by hand every day/week.
    """

    __tablename__ = "task_assignment"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE")
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_template.id", ondelete="CASCADE")
    )
    assignee_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE")
    )
    recurrence_rule: Mapped[str] = mapped_column(String(20))  # "daily" | "weekly"
    # Comma-separated day-of-week ints (0=Mon..6=Sun), "weekly" only. None means the
    # legacy single-weekday-of-start_date behavior (i.e. exactly once per week).
    weekdays: Mapped[str | None] = mapped_column(String(20), nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    template: Mapped["TaskTemplate"] = relationship()


class TaskInstance(Base, UUIDPk):
    __tablename__ = "task_instance"

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_template.id", ondelete="CASCADE")
    )
    assignee_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE")
    )
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_assignment.id", ondelete="CASCADE"), nullable=True
    )
    due_date: Mapped[date] = mapped_column(Date)
    # The day phase this instance belongs to (None = whole day). Deleting the phase keeps finished instances.
    phase_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("day_phase.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus, name="task_status"), default=TaskStatus.OPEN)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    template: Mapped["TaskTemplate"] = relationship()
