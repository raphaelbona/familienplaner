import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPk


class TimetableEntry(Base, UUIDPk):
    """A single class period entry in a child's timetable."""

    __tablename__ = "timetable_entry"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE")
    )
    child_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE")
    )
    day_of_week: Mapped[int] = mapped_column(Integer)  # 0=Monday, 6=Sunday
    start_time: Mapped[str] = mapped_column(String(5))  # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5))  # "HH:MM"
    subject: Mapped[str] = mapped_column(String(100))
    room: Mapped[str | None] = mapped_column(String(50), nullable=True)
