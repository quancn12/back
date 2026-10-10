"""Durable, versioned knowledge-base models for local and PostgreSQL deployments."""
from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.database import Base


def _new_id() -> str:
    return str(uuid.uuid4())


embedding_type = JSON().with_variant(
    Vector(settings.embedding_dimensions), "postgresql"
)


class SourceDocument(Base):
    """Stable identity of one source, independent from its content revisions."""

    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    source_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    primary_destination_id: Mapped[str] = mapped_column(
        ForeignKey("destinations.id"), index=True
    )
    filename: Mapped[str] = mapped_column(String)
    storage_path: Mapped[str] = mapped_column(String)
    mime_type: Mapped[str] = mapped_column(String, default="application/pdf")
    status: Mapped[str] = mapped_column(String, default="active", index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class DocumentVersion(Base):
    """Immutable revision of a source; only a completed revision becomes active."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("source_document_id", "version", name="uq_source_version"),
        UniqueConstraint(
            "source_document_id",
            "content_hash",
            "embedding_model",
            "chunker_version",
            name="uq_source_content_pipeline",
        ),
        Index("ix_document_versions_active", "source_document_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String, index=True)
    storage_path: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str] = mapped_column(String)
    chunker_version: Mapped[str] = mapped_column(String)
    tour_code: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    duration_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    locations: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IngestionJob(Base):
    """Persistent queue row; PostgreSQL workers can claim rows with SKIP LOCKED."""

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        Index(
            "ix_ingestion_jobs_status_heartbeat_at",
            "status",
            "heartbeat_at",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DocumentChunk(Base):
    """Searchable text and embedding belonging to a completed document version."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id", "chunk_index", name="uq_version_chunk_index"
        ),
        Index("ix_document_chunks_scope", "country_code", "destination_slug"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String, default="Tổng quan")
    text: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(embedding_type)
    country_code: Mapped[str] = mapped_column(String(2), default="VN", index=True)
    destination_slug: Mapped[str] = mapped_column(String, index=True)
    destination_name: Mapped[str] = mapped_column(String)
    province_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String)
    locations: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    scope_key: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_active: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
