"""Recording what a guest thought of an answer.

This is the smallest thing that turns "is the chatbot any good?" from an
opinion into a number. It is deliberately narrow:

* No free text. A comment box is an open-ended personal-data surface and a
  moderation queue; a fixed reason list answers the question the table exists
  for without either.
* One row per message. Rating the same answer again replaces the row, so the
  table holds a current opinion rather than a click log.
* Reasons are validated against the rating. "Thông tin sai" cannot be attached
  to a thumbs up, because a counting table that allows nonsense produces
  nonsense counts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.unified import (
    MessageFeedback,
    UnifiedConversation,
    UnifiedMessage,
    utcnow,
)

RATING_UP = "up"
RATING_DOWN = "down"
RATINGS = (RATING_UP, RATING_DOWN)

# Reasons are stored as codes. The wording a guest sees lives in the front end
# so it can be translated without a migration.
POSITIVE_REASONS: tuple[str, ...] = (
    "accurate",
    "complete",
    "well_presented",
)

NEGATIVE_REASONS: tuple[str, ...] = (
    "inaccurate",
    "incomplete",
    "off_topic",
    "too_long",
    "hard_to_read",
    # The system refuses when it has no grounding. That is usually correct and
    # is one of its better properties, so a guest reporting a refusal they
    # thought was wrong is the single most useful signal here: it names a gap
    # in the knowledge base rather than a fault in the model.
    "refused_wrongly",
)

REASONS_BY_RATING: dict[str, tuple[str, ...]] = {
    RATING_UP: POSITIVE_REASONS,
    RATING_DOWN: NEGATIVE_REASONS,
}


class FeedbackError(Exception):
    """A caller supplied something this table must not store."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class FeedbackRecord:
    id: str
    message_id: int
    conversation_id: str
    rating: str
    reason: str | None
    context_type: str
    locale: str
    created_at: datetime
    updated_at: datetime


def available_reasons() -> dict[str, list[str]]:
    """What the front end is allowed to offer, by rating."""
    return {rating: list(reasons) for rating, reasons in REASONS_BY_RATING.items()}


def validate(rating: str, reason: str | None) -> tuple[str, str | None]:
    """Normalise and check a submission before it reaches the database."""
    normalised_rating = (rating or "").strip().lower()
    if normalised_rating not in RATINGS:
        raise FeedbackError(
            f"Đánh giá phải là một trong {RATINGS}, nhận được {rating!r}."
        )
    normalised_reason = (reason or "").strip().lower() or None
    if normalised_reason is None:
        return normalised_rating, None
    allowed = REASONS_BY_RATING[normalised_rating]
    if normalised_reason not in allowed:
        raise FeedbackError(
            f"Lý do {normalised_reason!r} không hợp lệ cho đánh giá "
            f"{normalised_rating!r}. Hợp lệ: {allowed}."
        )
    return normalised_rating, normalised_reason


def _load_rated_message(
    db: Session, conversation_id: str, message_id: int
) -> UnifiedMessage:
    message = db.get(UnifiedMessage, message_id)
    if message is None:
        raise FeedbackError("Không tìm thấy tin nhắn.", status_code=404)
    # A message id from a different conversation is either a stale client or
    # someone walking the id space. Either way it is not this conversation's
    # to rate, and the 404 does not confirm the id exists elsewhere.
    if message.conversation_id != conversation_id:
        raise FeedbackError("Không tìm thấy tin nhắn.", status_code=404)
    if message.role != "assistant":
        raise FeedbackError(
            "Chỉ đánh giá được câu trả lời của trợ lý, không đánh giá câu hỏi.",
        )
    return message


def record_feedback(
    db: Session,
    *,
    conversation_id: str,
    message_id: int,
    rating: str,
    reason: str | None = None,
) -> FeedbackRecord:
    """Store or replace the rating for one assistant message."""
    normalised_rating, normalised_reason = validate(rating, reason)
    message = _load_rated_message(db, conversation_id, message_id)
    conversation = db.get(UnifiedConversation, conversation_id)
    if conversation is None:
        raise FeedbackError("Không tìm thấy hội thoại.", status_code=404)

    existing = db.scalar(
        select(MessageFeedback).where(MessageFeedback.message_id == message_id)
    )
    now = utcnow()
    if existing is None:
        existing = MessageFeedback(
            message_id=message_id,
            conversation_id=conversation_id,
            rating=normalised_rating,
            reason=normalised_reason,
            context_type=message.context_type,
            locale=conversation.locale,
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
    else:
        existing.rating = normalised_rating
        existing.reason = normalised_reason
        existing.context_type = message.context_type
        existing.locale = conversation.locale
        existing.updated_at = now
    db.commit()
    db.refresh(existing)
    return _to_record(existing)


def clear_feedback(db: Session, *, conversation_id: str, message_id: int) -> bool:
    """Withdraw a rating. Returns True when a row was actually removed."""
    _load_rated_message(db, conversation_id, message_id)
    existing = db.scalar(
        select(MessageFeedback).where(MessageFeedback.message_id == message_id)
    )
    if existing is None:
        return False
    db.delete(existing)
    db.commit()
    return True


def get_feedback(
    db: Session, *, conversation_id: str, message_id: int
) -> FeedbackRecord | None:
    _load_rated_message(db, conversation_id, message_id)
    existing = db.scalar(
        select(MessageFeedback).where(MessageFeedback.message_id == message_id)
    )
    return _to_record(existing) if existing is not None else None


def summarise(db: Session, *, context_type: str | None = None) -> dict[str, object]:
    """Aggregate counts.

    A thumbs-down button nobody reads is decoration, so the counting lives here
    rather than waiting for a dashboard. Returns totals, a satisfaction share,
    and the reason breakdown that says *how* answers are failing.
    """
    query = select(MessageFeedback.rating, func.count()).group_by(
        MessageFeedback.rating
    )
    if context_type:
        query = query.where(MessageFeedback.context_type == context_type)
    by_rating = {rating: count for rating, count in db.execute(query).all()}

    reason_query = select(
        MessageFeedback.rating, MessageFeedback.reason, func.count()
    ).group_by(MessageFeedback.rating, MessageFeedback.reason)
    if context_type:
        reason_query = reason_query.where(MessageFeedback.context_type == context_type)
    reasons: dict[str, dict[str, int]] = {RATING_UP: {}, RATING_DOWN: {}}
    for rating, reason, count in db.execute(reason_query).all():
        if reason is None:
            continue
        reasons.setdefault(rating, {})[reason] = count

    up = by_rating.get(RATING_UP, 0)
    down = by_rating.get(RATING_DOWN, 0)
    total = up + down
    return {
        "total": total,
        "up": up,
        "down": down,
        # None rather than 0 when nothing has been rated: an empty table means
        # "not measured yet", and reporting that as 0% satisfaction would be a
        # lie that looks like data.
        "satisfaction": round(up / total, 4) if total else None,
        "reasons": reasons,
        "context_type": context_type,
    }


def _to_record(row: MessageFeedback) -> FeedbackRecord:
    return FeedbackRecord(
        id=row.id,
        message_id=row.message_id,
        conversation_id=row.conversation_id,
        rating=row.rating,
        reason=row.reason,
        context_type=row.context_type,
        locale=row.locale,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
