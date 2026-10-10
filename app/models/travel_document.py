"""Source document tracked by the destination-centric VietSage pipeline."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _new_id() -> str:
    return str(uuid.uuid4())


class TravelDocument(Base):
    __tablename__ = "travel_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    primary_destination_id: Mapped[str] = mapped_column(
        ForeignKey("destinations.id"), index=True
    )
    filename: Mapped[str] = mapped_column(String)
    source_path: Mapped[str | None] = mapped_column(String, nullable=True)
    content_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    document_type: Mapped[str] = mapped_column(String, default="tour_program")
    tour_code: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    duration_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    locations: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str | None] = mapped_column(String, nullable=True)
    chunker_version: Mapped[str | None] = mapped_column(String, nullable=True)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

