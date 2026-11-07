import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPk


class PasswordResetToken(Base, UUIDPk, TimestampMixin):
    """Single-use, time-limited token for the parent password-reset flow.

    Stores a SHA-256 hash of the raw token (not bcrypt): the token itself is a
    32-byte random value with no guessable structure, so a fast deterministic
    hash is enough to look it up safely while never persisting it in plaintext.
    """

    __tablename__ = "password_reset_token"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
