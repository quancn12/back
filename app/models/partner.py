"""Partner websites that call the chat API from their own servers.

A partner builds its own chat UI and calls VietSage server to server with a
secret API key. Only a SHA-256 digest of the key is stored: the key is shown
once when it is issued or rotated. Partners are deactivated, never deleted, so
their conversations keep a valid owner.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.unified import _new_id, utcnow


class Partner(Base):
    __tablename__ = "partners"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    # The public, non-secret part of the key ("vsk_1a2b3c4d") that finds the row.
    key_prefix: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
