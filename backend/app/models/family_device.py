import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPk

DEVICE_TTL_DAYS = 365


def _default_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=DEVICE_TTL_DAYS)


class FamilyDevice(Base, UUIDPk, TimestampMixin):
    """A device a parent has approved for the children's login (avatar picker + PIN login).

    Only the SHA-256 hash of the random device token is stored; the token itself is shown once at pairing.
    """

    __tablename__ = "family_device"

    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("family.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(60))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_default_expiry)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    # wrong PIN logins from this device in the current window (throttle against guessing across users)
    failed_pin_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    pin_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
