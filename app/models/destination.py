"""Geographic destination model for the VietSage travel knowledge base."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _new_id() -> str:
    return str(uuid.uuid4())


class Destination(Base):
    __tablename__ = "destinations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    slug: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    destination_type: Mapped[str] = mapped_column(String, default="region")
    country_code: Mapped[str] = mapped_column(String(2), default="VN", index=True)
    country_name: Mapped[str] = mapped_column(String, default="Việt Nam")
    province_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("destinations.id"), nullable=True, index=True
    )
    aliases: Mapped[str | None] = mapped_column(String, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

