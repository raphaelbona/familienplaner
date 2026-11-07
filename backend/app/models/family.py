from typing import TYPE_CHECKING

from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPk

if TYPE_CHECKING:
    from app.models.user import User


class Family(Base, UUIDPk, TimestampMixin):
    __tablename__ = "family"

    name: Mapped[str] = mapped_column(String(120))
    hosting_mode: Mapped[str] = mapped_column(String(20), default="self_hosted")
    locale: Mapped[str] = mapped_column(String(10), default="de-CH")

    # Used for the home dashboard's weather widget; unset until a parent configures it.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    users: Mapped[list["User"]] = relationship(back_populates="family")
