"""
Model khách sạn. Mỗi khách sạn là 1 "tenant" độc lập — mọi dữ liệu khác
(documents, service_mentions, vector chunks) đều gắn với hotel_id để tách biệt.
"""
import uuid
from datetime import datetime, time
from sqlalchemy import String, DateTime, Time, Float
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _new_id() -> str:
    return str(uuid.uuid4())


class Hotel(Base):
    __tablename__ = "hotels"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)

    # slug dùng trong link QR, vd: domain.com/chat?hotel=hotel-a-danang
    slug: Mapped[str] = mapped_column(String, unique=True, index=True)

    name: Mapped[str] = mapped_column(String)
    city: Mapped[str] = mapped_column(String, nullable=True)

    checkin_time: Mapped[time] = mapped_column(Time, nullable=True)
    checkout_time: Mapped[time] = mapped_column(Time, nullable=True)

    # ngôn ngữ mặc định + danh sách hỗ trợ (lưu dạng chuỗi CSV cho đơn giản, vd: "vi,en,ko")
    default_language: Mapped[str] = mapped_column(String, default="vi")
    supported_languages: Mapped[str] = mapped_column(String, default="vi,en")

    latitude: Mapped[float] = mapped_column(Float, nullable=True)
    longitude: Mapped[float] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
