"""Identity, opaque cookie sessions, CSRF, and provider login helpers."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

import httpx
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.auth import AppSession, User, utcnow


_password_hasher = PasswordHasher(type=Type.ID)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _password_hash(password: str) -> str:
    return _password_hasher.hash(password)


def _password_ok(password: str, encoded: str | None) -> bool:
    try:
        return _password_hasher.verify(encoded, password)  # type: ignore[arg-type]
    except (AttributeError, ValueError, TypeError, VerifyMismatchError, VerificationError):
        return False


@dataclass
class AuthContext:
    session: AppSession
    user: User | None
    csrf_token: str
    session_token: str

    @property
    def user_id(self) -> str | None:
        return self.user.id if self.user else None

    @property
    def is_guest(self) -> bool:
        return self.user is None


def _cookie_options() -> dict:
    return {
        "httponly": True,
        "secure": settings.auth_cookie_secure,
        "samesite": settings.auth_cookie_samesite,
        "path": "/",
    }


def _csrf_for_session_token(token: str) -> str:
    return hmac.new(
        settings.auth_secret.encode("utf-8"),
        f"csrf:{token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _set_session_cookies(
    response: Response,
    token: str,
    csrf: str,
    max_age: int,
) -> None:
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        max_age=max_age,
        **_cookie_options(),
    )
    # CSRF remains only in runtime JavaScript memory. A legacy cookie is
    # explicitly removed during the transition to the derived token design.
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def _new_session(
    db: Session,
    user: User | None = None,
) -> tuple[AppSession, str, str]:
    token = secrets.token_urlsafe(48)
    csrf = _csrf_for_session_token(token)
    row = AppSession(
        token_hash=_hash(token),
        csrf_token_hash=_hash(csrf),
        user_id=user.id if user else None,
        is_guest=user is None,
        expires_at=utcnow() + timedelta(hours=settings.auth_session_ttl_hours),
    )
    db.add(row)
    db.flush()
    return row, token, csrf


def load_context(request: Request, db: Session) -> AuthContext | None:
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        return None
    row = db.scalar(
        select(AppSession).where(
            AppSession.token_hash == _hash(token),
            AppSession.revoked_at.is_(None),
        )
    )
    if row is None or row.expires_at <= utcnow():
        return None
    csrf = _csrf_for_session_token(token)
    if not hmac.compare_digest(_hash(csrf), row.csrf_token_hash):
        # AUTH_SECRET rotation invalidates the prior browser session instead of
        # leaving it alive with an unusable CSRF verifier.
        row.revoked_at = utcnow()
        db.flush()
        return None
    row.last_seen_at = utcnow()
    user = db.get(User, row.user_id) if row.user_id else None
    if user is not None and not user.is_active:
        return None
    return AuthContext(row, user, csrf, token)


def get_current_app_session(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthContext:
    """Return authenticated or guest context; lazily creates a guest session.

    Conversation routes can use ``context.user_id`` as the durable ownership
    claim. Guests intentionally have ``None`` until they sign in.
    """
    context = load_context(request, db)
    if context is None:
        row, token, csrf = _new_session(db)
        _set_session_cookies(
            response,
            token,
            csrf,
            settings.auth_session_ttl_hours * 3600,
        )
        db.commit()
        return AuthContext(row, None, csrf, token)
    db.commit()
    return context


def require_csrf(
    request: Request,
    context: AuthContext = Depends(get_current_app_session),
) -> AuthContext:
    supplied = request.headers.get("X-CSRF-Token") or request.headers.get("X-CSRFToken")
    if (
        not supplied
        or not hmac.compare_digest(supplied, context.csrf_token)
        or not hmac.compare_digest(_hash(supplied), context.session.csrf_token_hash)
    ):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")
    return context


def issue_user_session(db: Session, response: Response, user: User) -> str:
    _, token, csrf = _new_session(db, user)
    _set_session_cookies(
        response,
        token,
        csrf,
        settings.auth_session_ttl_hours * 3600,
    )
    return csrf


def claim_guest_conversations(db: Session, guest_session_id: str, user_id: str) -> int:
    """Claim only ownerless conversations from the exact prior guest session."""
    from app.models.unified import UnifiedConversation

    rows = db.scalars(
        select(UnifiedConversation).where(
            UnifiedConversation.owner_session_id == guest_session_id,
            UnifiedConversation.owner_user_id.is_(None),
        )
    ).all()
    for row in rows:
        row.owner_user_id = user_id
        row.owner_session_id = None
    return len(rows)


async def google_exchange(code: str, redirect_uri: str) -> dict:
    async with httpx.AsyncClient(timeout=settings.auth_http_timeout_seconds) as client:
        try:
            token = await client.post(
                settings.google_token_url,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            token.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(400, "Google token exchange failed") from exc
        token_data = token.json()
        encoded_id_token = token_data.get("id_token")
        if not encoded_id_token:
            raise HTTPException(400, "Google token exchange failed")
        # Google publishes rotating signing keys; google-auth verifies the
        # signature, issuer, audience, and exp instead of trusting userinfo.
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import id_token as google_id_token
        try:
            claims = google_id_token.verify_oauth2_token(
                encoded_id_token,
                GoogleRequest(),
                settings.google_client_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, "Invalid Google ID token") from exc
        if claims.get("iss") not in {
            "accounts.google.com",
            "https://accounts.google.com",
        } or not claims.get("email_verified"):
            raise HTTPException(400, "Google account is not verified")
        return claims
