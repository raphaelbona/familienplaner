import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPk

if TYPE_CHECKING:
    from app.models.family import Family


class UserRole(str, enum.Enum):
    PARENT_ADMIN = "parent_admin"
    PARENT_MEMBER = "parent_member"
    DASHBOARD = "dashboard"
    CHILD = "child"


class User(Base, UUIDPk, TimestampMixin):
    __tablename__ = "user"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE")
    )
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"))
    display_name: Mapped[str] = mapped_column(String(80))
    avatar_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # parents authenticate with email + password, children with a short PIN
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pin_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # PIN brute-force lockout (see app/core/pin_lockout.py)
    failed_pin_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    pin_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pin_lock_level: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    family: Mapped["Family"] = relationship(back_populates="users")
