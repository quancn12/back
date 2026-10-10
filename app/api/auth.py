"""Public identity and opaque browser-session API."""
from __future__ import annotations

import hmac
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.auth import OAuthIdentity, OAuthState, PasswordCredential, User, utcnow
from app.models.auth_schemas import (
    AuthResponse,
    AuthCapabilities,
    LoginRequest,
    SessionResponse,
    SignupRequest,
    UserResponse,
)
from app.services.auth import (
    AuthContext,
    _hash,
    _new_session,
    _password_hash,
    _password_ok,
    _set_session_cookies,
    claim_guest_conversations,
    get_current_app_session,
    google_exchange,
    issue_user_session,
    require_csrf,
)
from app.services.auth_throttle import auth_throttle


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _capabilities() -> AuthCapabilities:
    return AuthCapabilities(password=True, google=settings.google_auth_available)


def _user(db: Session, user: User) -> UserResponse:
    methods = []
    if db.get(PasswordCredential, user.id) is not None:
        methods.append("password")
    methods.extend(
        db.scalars(
            select(OAuthIdentity.provider).where(OAuthIdentity.user_id == user.id)
        ).all()
    )
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_guest=False,
        authentication_methods=sorted(set(methods)),
    )


def _rotate_to_user(
    db: Session,
    response: Response,
    previous: AuthContext,
    user: User,
) -> str:
    """Claim only the current guest's conversations, then rotate the session."""
    csrf = issue_user_session(db, response, user)
    if previous.user is None:
        claim_guest_conversations(db, previous.session.id, user.id)
    previous.session.revoked_at = utcnow()
    return csrf


@router.get("/session", response_model=SessionResponse)
def session(
    response: Response,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_current_app_session),
):
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(
        authenticated=context.user is not None,
        user=_user(db, context.user) if context.user else None,
        csrf_token=context.csrf_token,
        capabilities=_capabilities(),
    )


@router.post("/signup", response_model=AuthResponse, status_code=201)
def signup(
    payload: SignupRequest,
    response: Response,
    db: Session = Depends(get_db),
    previous: AuthContext = Depends(require_csrf),
):
    response.headers["Cache-Control"] = "no-store"
    if db.scalar(select(User.id).where(User.email_normalized == payload.email)):
        raise HTTPException(status_code=409, detail="Account cannot be created.")
    user = User(
        email=payload.email,
        email_normalized=payload.email,
        display_name=(payload.display_name or "").strip() or None,
    )
    try:
        db.add(user)
        db.flush()
        db.add(
            PasswordCredential(
                user_id=user.id,
                password_hash=_password_hash(payload.password),
            )
        )
        csrf = _rotate_to_user(db, response, previous, user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Account cannot be created.") from exc
    return AuthResponse(user=_user(db, user), csrf_token=csrf)


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    previous: AuthContext = Depends(require_csrf),
):
    response.headers["Cache-Control"] = "no-store"
    throttle_key = _hash(
        f"{request.client.host if request.client else 'unknown'}:{payload.email}"
    )
    auth_throttle.check(throttle_key)
    user = db.scalar(select(User).where(User.email_normalized == payload.email))
    credential = db.get(PasswordCredential, user.id) if user else None
    if (
        user is None
        or not user.is_active
        or credential is None
        or not _password_ok(payload.password, credential.password_hash)
    ):
        auth_throttle.failure(throttle_key)
        raise HTTPException(status_code=401, detail="Authentication failed.")
    auth_throttle.success(throttle_key)
    csrf = _rotate_to_user(db, response, previous, user)
    db.commit()
    return AuthResponse(user=_user(db, user), csrf_token=csrf)


@router.post("/logout", response_model=SessionResponse)
def logout(
    response: Response,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(require_csrf),
):
    response.headers["Cache-Control"] = "no-store"
    context.session.revoked_at = utcnow()
    _, token, csrf = _new_session(db)
    _set_session_cookies(
        response,
        token,
        csrf,
        settings.auth_session_ttl_hours * 3600,
    )
    db.commit()
    return SessionResponse(
        authenticated=False,
        user=None,
        csrf_token=csrf,
        capabilities=_capabilities(),
    )


@router.get("/me", response_model=SessionResponse)
def me(
    response: Response,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_current_app_session),
):
    response.headers["Cache-Control"] = "no-store"
    if context.user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return SessionResponse(
        authenticated=True,
        user=_user(db, context.user),
        csrf_token=context.csrf_token,
        capabilities=_capabilities(),
    )


@router.get("/google/start")
def google_start(
    context: AuthContext = Depends(get_current_app_session),
    db: Session = Depends(get_db),
):
    if not settings.google_auth_available:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured.")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    redirect_uri = settings.google_redirect_uri
    db.add(
        OAuthState(
            state_hash=_hash(state),
            session_id=context.session.id,
            provider="google",
            redirect_uri=redirect_uri,
            nonce=nonce,
            expires_at=utcnow() + timedelta(minutes=10),
        )
    )
    db.commit()
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
        }
    )
    response = RedirectResponse(
        f"{settings.google_authorize_url}?{query}",
        status_code=302,
    )
    response.set_cookie(
        "vietsage_oauth_state",
        state,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=600,
        path="/",
    )
    _set_session_cookies(
        response,
        context.session_token,
        context.csrf_token,
        max(1, int((context.session.expires_at - utcnow()).total_seconds())),
    )
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str = Query(...),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
    previous: AuthContext = Depends(get_current_app_session),
):
    cookie_state = request.cookies.get("vietsage_oauth_state")
    if not cookie_state or not hmac.compare_digest(cookie_state, state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")
    state_row = db.scalar(
        select(OAuthState).where(
            OAuthState.state_hash == _hash(state),
            OAuthState.session_id == previous.session.id,
            OAuthState.provider == "google",
            OAuthState.consumed_at.is_(None),
        )
    )
    if state_row is None or state_row.expires_at <= utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    state_row.consumed_at = utcnow()
    db.commit()

    if error or not code:
        response = RedirectResponse("/app?auth_error=GOOGLE_CANCELLED", status_code=303)
        response.delete_cookie("vietsage_oauth_state", path="/")
        return response

    claims = await google_exchange(code, state_row.redirect_uri)
    if not hmac.compare_digest(str(claims.get("nonce", "")), state_row.nonce):
        raise HTTPException(status_code=400, detail="Invalid Google ID token.")
    subject = str(claims.get("sub", ""))
    email = str(claims.get("email", "")).strip().casefold()
    if not subject or not email or claims.get("email_verified") is not True:
        raise HTTPException(status_code=400, detail="Google account is not verified.")

    identity = db.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == "google",
            OAuthIdentity.provider_subject == subject,
        )
    )
    if identity is not None:
        user = db.get(User, identity.user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="Authentication failed.")
    else:
        existing_email = db.scalar(
            select(User.id).where(User.email_normalized == email)
        )
        if existing_email is not None:
            response = RedirectResponse(
                "/app?auth_error=ACCOUNT_LINK_REQUIRED",
                status_code=303,
            )
            response.delete_cookie("vietsage_oauth_state", path="/")
            return response
        try:
            user = User(
                email=email,
                email_normalized=email,
                display_name=str(claims.get("name") or "").strip() or None,
                avatar_url=str(claims.get("picture") or "").strip() or None,
                email_verified_at=utcnow(),
            )
            db.add(user)
            db.flush()
            db.add(
                OAuthIdentity(
                    user_id=user.id,
                    provider="google",
                    provider_subject=subject,
                    provider_email=email,
                )
            )
            db.flush()
        except IntegrityError:
            db.rollback()
            identity = db.scalar(
                select(OAuthIdentity).where(
                    OAuthIdentity.provider == "google",
                    OAuthIdentity.provider_subject == subject,
                )
            )
            if identity is None:
                raise HTTPException(
                    status_code=409,
                    detail="OAuth identity conflict.",
                )
            user = db.get(User, identity.user_id)
            if user is None or not user.is_active:
                raise HTTPException(status_code=401, detail="Authentication failed.")

    response = RedirectResponse("/app?auth=success", status_code=303)
    try:
        _rotate_to_user(db, response, previous, user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="OAuth identity conflict.") from exc
    response.delete_cookie("vietsage_oauth_state", path="/")
    return response
