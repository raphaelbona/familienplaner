import uuid
from datetime import time

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Time
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPk

DAY_TYPE_SCHOOL = "school"
DAY_TYPE_FREE = "free"
DAY_TYPE_BOTH = "both"


class DayPhase(Base, UUIDPk, TimestampMixin):
    """A part of the day ("Vor der Schule", "Mittag", ...) a family splits its tasks into.

    School days and free days (weekend, holidays) can have different phases: ``day_type`` says on which kind
    of day the phase exists. Both times are on the same day (``end_time`` is after ``start_time``).
    """

    __tablename__ = "day_phase"
    __table_args__ = (
        CheckConstraint("end_time > start_time", name="ck_day_phase_times"),
        CheckConstraint("day_type IN ('school', 'free', 'both')", name="ck_day_phase_day_type"),
    )

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(40))
    icon: Mapped[str | None] = mapped_column(String(8), nullable=True)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    # "school" | "free" | "both"
    day_type: Mapped[str] = mapped_column(String(10), default=DAY_TYPE_BOTH, server_default=DAY_TYPE_BOTH)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
