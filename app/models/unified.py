"""Unified stay, conversation, privacy, and hotel-service models."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Naive UTC for compatibility with the repository's existing columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class HotelRoom(Base):
    __tablename__ = "hotel_rooms"
    __table_args__ = (UniqueConstraint("hotel_id", "room_number", name="uq_hotel_room"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id"), index=True)
    room_number: Mapped[str] = mapped_column(String, index=True)
    connect_code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    verification_pin_hash: Mapped[str] = mapped_column(String(160))
    # Reception's unlock: wrong PINs entered before this moment stop counting
    # towards the lockout, but stay on record.
    pin_lock_cleared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Stay(Base):
    __tablename__ = "stays"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id"), index=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("hotel_rooms.id"), index=True)
    guest_alias: Mapped[str] = mapped_column(String, default="Khách lưu trú")
    check_in_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    check_out_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", index=True)
    # A PIN issued for this stay only. When set, the room's standing PIN no
    # longer opens this stay, so the next guest never inherits the last one's.
    verification_pin_hash: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StayConnectionChallenge(Base):
    __tablename__ = "stay_connection_challenges"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    room_id: Mapped[str] = mapped_column(ForeignKey("hotel_rooms.id"), index=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StayAccessGrant(Base):
    __tablename__ = "stay_access_grants"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    stay_id: Mapped[str] = mapped_column(ForeignKey("stays.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[str] = mapped_column(
        String, default="hotel:read,service:create,service:read"
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HotelKnowledgeItem(Base):
    __tablename__ = "hotel_knowledge_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id"), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(String(24), default="PUBLIC", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class HotelServiceItem(Base):
    __tablename__ = "hotel_service_items"
    __table_args__ = (UniqueConstraint("hotel_id", "code", name="uq_hotel_service_code"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id"), index=True)
    code: Mapped[str] = mapped_column(String, index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="VND")
    hours: Mapped[str] = mapped_column(String, default="24/7")
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class UnifiedConversation(Base):
    __tablename__ = "unified_conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    # Ownerless legacy rows deliberately fail closed at the API boundary. New
    # rows always receive exactly one owner: an account or guest AppSession
    # (PWA), or a partner plus that partner's user (partner API). The two
    # owner kinds never see each other's rows.
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    owner_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # partners.id (partners are deactivated, never deleted) and a SHA-256 of
    # "<partner id>:<partner's own user id>", so the raw user id is not stored.
    owner_partner_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    owner_partner_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), default="VN")
    destination_slug: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    active_stay_id: Mapped[str | None] = mapped_column(
        ForeignKey("stays.id"), nullable=True, index=True
    )
    preferred_mode: Mapped[str] = mapped_column(String(16), default="TRAVEL")
    locale: Mapped[str] = mapped_column(String(12), default="vi")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_active: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, index=True
    )


class UnifiedMessage(Base):
    __tablename__ = "unified_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("unified_conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    context_type: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    __table_args__ = (
        UniqueConstraint("conversation_id", "purpose", name="uq_conversation_consent"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("unified_conversations.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="DENIED")
    policy_version: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32), default="privacy_center")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MessageFeedback(Base):
    """One rating per assistant turn.

    Deliberately carries no free text. A comment box would collect whatever a
    guest chooses to type, which is an open-ended personal-data surface and a
    moderation problem; a fixed reason list answers the question this table
    exists for -- which answers are wrong, and in what way -- without either.

    One row per message, replaced when the same message is rated again, so the
    table holds a current opinion rather than a click log.
    """

    __tablename__ = "message_feedback"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_message_feedback_message"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("unified_messages.id", ondelete="CASCADE"), index=True
    )
    # Denormalised so the summary can group by conversation without a join,
    # and so a deleted conversation's rows are still recognisable while the
    # cascade runs.
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("unified_conversations.id", ondelete="CASCADE"), index=True
    )
    rating: Mapped[str] = mapped_column(String(8), index=True)
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # Which answer was rated: mode and locale make the counts readable without
    # reopening the conversation itself.
    context_type: Mapped[str] = mapped_column(String(16), default="TRAVEL", index=True)
    locale: Mapped[str] = mapped_column(String(12), default="vi")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ServiceRequest(Base):
    __tablename__ = "service_requests"
    __table_args__ = (
        UniqueConstraint("stay_id", "idempotency_key", name="uq_stay_idempotency"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id"), index=True)
    stay_id: Mapped[str] = mapped_column(ForeignKey("stays.id"), index=True)
    service_item_id: Mapped[str] = mapped_column(
        ForeignKey("hotel_service_items.id"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="SUBMITTED", index=True)
    total_amount: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
