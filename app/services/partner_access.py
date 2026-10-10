"""Partner API: key authentication, per-partner rate limit, conversation owner.

Partners call from their own servers with ``Authorization: Bearer <key>`` and
name their own user in ``X-Partner-User``. No cookie and no CSRF token are
involved: nothing here is sent automatically by a browser. A conversation is
reachable only with the key of the partner that created it and the same user
id; everything else answers 404, as the PWA's ownership boundary does.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import deque
from datetime import timedelta

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.partner import Partner
from app.models.unified import UnifiedConversation, utcnow

_KEY_RE = re.compile(r"^(vsk_[0-9a-f]{8})_[A-Za-z0-9_-]{32,128}$")
_USER_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")
_UNAUTHORIZED = HTTPException(
    status_code=401,
    detail="Partner API key is missing, invalid or deactivated.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def issue_partner_key() -> tuple[str, str, str]:
    """Return ``(key, public prefix, digest)``. Only the digest is stored."""

    prefix = f"vsk_{secrets.token_hex(4)}"
    key = f"{prefix}_{secrets.token_urlsafe(32)}"
    return key, prefix, _digest(key)


def partner_for_key(db: Session, key: str | None) -> Partner | None:
    match = _KEY_RE.match(key or "")
    if match is None:
        return None
    partner = db.scalar(select(Partner).where(Partner.key_prefix == match.group(1)))
    if partner is None or not partner.is_active:
        return None
    if not hmac.compare_digest(partner.key_hash, _digest(key)):
        return None
    return partner


class PartnerRateLimiter:
    """Sliding one-minute window per partner, per server process.

    With several workers each keeps its own window, so the effective ceiling
    is the configured limit times the worker count; the shared chat-capacity
    limit still bounds concurrent model work.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, deque[float]] = {}

    def check(self, partner_id: str, limit: int, now: float | None = None) -> int:
        """Record a call; return 0 when allowed, else seconds until a slot frees."""

        now = time.monotonic() if now is None else now
        with self._lock:
            calls = self._calls.setdefault(partner_id, deque())
            while calls and now - calls[0] >= 60:
                calls.popleft()
            if len(calls) >= max(1, limit):
                return max(1, int(60 - (now - calls[0])) + 1)
            calls.append(now)
            return 0

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()


partner_rate_limiter = PartnerRateLimiter()


def require_partner(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Partner:
    scheme, _, key = (authorization or "").partition(" ")
    partner = partner_for_key(db, key.strip()) if scheme.lower() == "bearer" else None
    if partner is None:
        raise _UNAUTHORIZED
    retry_after = partner_rate_limiter.check(partner.id, partner.rate_limit_per_minute)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Partner rate limit reached; retry later.",
            headers={"Retry-After": str(retry_after)},
        )
    now = utcnow()
    # One write a minute is enough to show an operator the key is in use.
    if partner.last_used_at is None or now - partner.last_used_at > timedelta(minutes=1):
        partner.last_used_at = now
        db.commit()
    return partner


def require_partner_user(
    x_partner_user: str | None = Header(default=None, alias="X-Partner-User"),
) -> str:
    """The partner's own id for its end user (opaque to VietSage, 1-128 chars)."""

    value = (x_partner_user or "").strip()
    if not _USER_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail="X-Partner-User header is required: your own id for this user, 1-128 characters.",
        )
    return value


def partner_user_ref(partner: Partner, user_id: str) -> str:
    """What is stored for the partner's user: a digest, never the raw id."""

    return _digest(f"{partner.id}:{user_id}")


def resolve_partner_conversation(
    db: Session,
    conversation_id: str,
    partner: Partner,
    user_id: str,
) -> UnifiedConversation:
    conversation = db.scalar(
        select(UnifiedConversation).where(
            UnifiedConversation.id == conversation_id,
            UnifiedConversation.owner_partner_id == partner.id,
            UnifiedConversation.owner_partner_user == partner_user_ref(partner, user_id),
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation
