import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPk


class PushSubscription(Base, UUIDPk, TimestampMixin):
    """A browser's Web Push subscription for one user (a user may have several devices)."""

    __tablename__ = "push_subscription"
    __table_args__ = (UniqueConstraint("user_id", "endpoint", name="uq_push_subscription_user_endpoint"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"))
    endpoint: Mapped[str] = mapped_column(String(500))
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
