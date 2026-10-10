"""Session-scoped research API. Possessing a conversation ID grants no access."""
import copy
import hashlib
import secrets
import time
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.proactive import ProactiveAudit, ProactiveSession
from app.services.proactive import controller

router = APIRouter(prefix="/api/v1/proactive", tags=["proactive-research"])


def now_minutes():
    return time.time() / 60


def available():
    return settings.proactive_research_enabled


def fixture_allowed():
    return settings.proactive_fixture_enabled and settings.app_env == "development"


class ModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["OFF", "SHADOW", "LIVE"]
    consent: bool = False


class AttentionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attention_available: bool = False


class ReceiptBody(AttentionBody):
    event_id: str = Field(min_length=1, max_length=64)


class ReplyBody(AttentionBody):
    transaction_id: str = Field(min_length=1, max_length=64)
    reply_id: str = Field(min_length=1, max_length=64)
    answer: Literal["yes", "no", "uncertain", "abort"]


class FixtureBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Literal["offer", "question"] = "offer"


def session_row(response: Response, x_research_token: str | None = Header(default=None, alias="X-Research-Token"), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    if not x_research_token or len(x_research_token) > 128:
        raise HTTPException(401, "Research session required")
    key = hashlib.sha256(x_research_token.encode()).hexdigest()
    row = db.get(ProactiveSession, key)
    if row is None or row.expires_at <= now_minutes():
        raise HTTPException(401, "Research session expired or invalid")
    return row


def mutate(db, row, operation):
    """Compare-and-swap prevents duplicate decisions/receipts across workers."""
    revision = row.revision
    state = copy.deepcopy(row.state)
    try:
        result, record = operation(state)
        changed = db.execute(update(ProactiveSession).where(ProactiveSession.token_hash == row.token_hash,
                             ProactiveSession.revision == revision).values(state=state, revision=revision + 1),
                             execution_options={"synchronize_session": False})
        if changed.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "Session changed; retry with the same idempotency ID")
        if record:
            db.add(ProactiveAudit(id=str(uuid4()), session_id=row.audit_id, timestamp=now_minutes(), record=record))
        db.commit()
        return result
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        # Never include raw exception text, payloads, tokens, or guest content.
        import logging
        logging.getLogger(__name__).error("proactive_failure type=%s", type(exc).__name__)
        return {"event": None, "status": "PROACTIVE_ERROR"}


@router.get("/availability")
def availability(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"available": available(), "ui_toggle": settings.proactive_ui_toggle_enabled,
            "fixture_available": available() and fixture_allowed(), "default_mode": "OFF"}


@router.post("/sessions")
def create_session(response: Response, db: Session = Depends(get_db)):
    if not available():
        raise HTTPException(404, "Research feature unavailable")
    token = secrets.token_urlsafe(32)
    db.add(ProactiveSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), audit_id=str(uuid4()),
                            expires_at=now_minutes() + 480, revision=0, state=controller.initial_state()))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"token": token, "mode": "OFF"}


@router.get("/mode")
def get_mode(row=Depends(session_row)):
    return {"mode": row.state["mode"] if available() else "OFF", "available": available(),
            "fixture": bool(row.state.get("fixture")) and fixture_allowed()}


@router.put("/mode")
def set_mode(body: ModeBody, row=Depends(session_row), db: Session = Depends(get_db)):
    if body.mode != "OFF" and (not available() or not body.consent):
        raise HTTPException(409, "Explicit research consent and feature availability required")
    if body.mode == "SHADOW" and not settings.proactive_shadow_enabled:
        raise HTTPException(403, "Internal SHADOW mode unavailable")
    def operation(state):
        controller.change_mode(state, body.mode, now_minutes())
        return {"mode": body.mode}, controller.audit_record(state, now_minutes(), "mode", consent_record=state["consent_record"])
    return mutate(db, row, operation)


@router.post("/poll")
def poll(body: AttentionBody, row=Depends(session_row), db: Session = Depends(get_db)):
    if not available() or row.state["mode"] == "OFF":
        return {"event": None, "status": "OFF"}
    return mutate(db, row, lambda s: controller.poll(s, now_minutes(), body.attention_available, fixture_allowed()))


@router.post("/receipt")
def receipt(body: ReceiptBody, row=Depends(session_row), db: Session = Depends(get_db)):
    if not available():
        return {"status": "SUPPRESSED"}
    return mutate(db, row, lambda s: controller.acknowledge(s, body.event_id, now_minutes(), body.attention_available, fixture_allowed()))


@router.post("/reply")
def reply(body: ReplyBody, row=Depends(session_row), db: Session = Depends(get_db)):
    if not available():
        return {"event": None, "status": "OFF"}
    return mutate(db, row, lambda s: controller.reply(s, body.transaction_id, body.answer, body.reply_id, now_minutes(), body.attention_available, fixture_allowed()))


@router.post("/fixture")
def set_fixture(body: FixtureBody, row=Depends(session_row), db: Session = Depends(get_db)):
    if not available() or not fixture_allowed():
        raise HTTPException(404, "Development fixture unavailable")
    def operation(state):
        if state.get("fixture"):
            return {"status": "ALREADY_LOADED"}, None
        state["fixture"] = {"origin": now_minutes(), "question": body.scenario == "question"}
        return {"status": "D0-MATH_LOADED"}, controller.audit_record(state, now_minutes(), "fixture", source_kind="D0-MATH")
    return mutate(db, row, operation)
