"""Capability-protected session state and content-free research decision audit."""
from sqlalchemy import Float, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProactiveSession(Base):
    __tablename__ = "proactive_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    audit_id: Mapped[str] = mapped_column(String(36), unique=True)
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[dict] = mapped_column(JSON)


class ProactiveAudit(Base):
    __tablename__ = "proactive_audits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    timestamp: Mapped[float] = mapped_column(Float, index=True)
    record: Mapped[dict] = mapped_column(JSON)
