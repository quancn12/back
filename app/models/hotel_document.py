"""
Track uploaded PDF files for each hotel and their processing status.
"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _new_id() -> str:
    return str(uuid.uuid4())


class HotelDocument(Base):
    __tablename__ = "hotel_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id"), index=True)

    filename: Mapped[str] = mapped_column(String)
    content_hash: Mapped[str | None] = mapped_column(String, index=True, nullable=True)

    # pending -> processing -> done / failed
    status: Mapped[str] = mapped_column(String, default="pending")
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    chunk_count: Mapped[int] = mapped_column(Integer, default=0)

    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
