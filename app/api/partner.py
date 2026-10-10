"""Partner chat API (option D): partner websites with their own chat UI.

The partner's server calls these routes with its secret key; its browser never
does. Answers come from the same planner, persistence and SSE events as the
PWA (``unified.prepare_chat_turn``), limited to TRAVEL mode: hotel services
need a verified stay, which only the PWA can establish. Guide for partners:
docs/human/PARTNER_API.md.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.unified import chat_stream_response, prepare_chat_turn
from app.core.database import get_db
from app.core.security import require_admin
from app.models.generative_ui import StructuredPlanArtifact, TravelItineraryArtifact
from app.models.partner import Partner
from app.models.unified import (
    ConsentRecord,
    MessageFeedback,
    UnifiedConversation,
    UnifiedMessage,
)
from app.models.unified_schemas import UnifiedChatRequest
from app.services.partner_access import (
    issue_partner_key,
    partner_user_ref,
    require_partner,
    require_partner_user,
    resolve_partner_conversation,
)

router = APIRouter(prefix="/api/v1", tags=["partner-api"])
Locale = Literal["vi", "en", "zh-Hans"]
MAX_HISTORY = 200


class PartnerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    name: str = Field(min_length=1, max_length=120)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=6000)


class PartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    slug: str
    name: str
    key_prefix: str
    rate_limit_per_minute: int
    is_active: bool
    created_at: datetime | None
    last_used_at: datetime | None


class PartnerKeyOut(PartnerOut):
    api_key: str = Field(description="Shown once. Store it on the partner's server only.")


class PartnerConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locale: Locale = "vi"
    destination_slug: str | None = Field(default=None, max_length=64)


class PartnerConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    locale: str
    destination_slug: str | None
    created_at: datetime | None


class PartnerMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    role: str
    content: str
    created_at: datetime | None


class PartnerChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    message: str = Field(min_length=1, max_length=4000)
    locale: Locale | None = None
    destination_slug: str | None = Field(default=None, max_length=64)
    # The itinerary/plan the user is looking at, sent back verbatim for a chat
    # edit ("đổi ngày 2 sang buổi chiều"); validated, never trusted.
    current_artifact: TravelItineraryArtifact | StructuredPlanArtifact | None = Field(
        default=None, alias="currentArtifact"
    )


class PartnerChatReply(BaseModel):
    conversation_id: str
    message_id: int | None
    reply: str
    suggestions: list[dict] = []
    places: list[dict] = []
    action: dict | None = None
    artifact_id: str | None = None
    artifact_version: int | None = None


# --- Operator: issue, rotate and deactivate partner keys (X-Admin-Key) ------

def _partner(db: Session, slug: str) -> Partner:
    partner = db.scalar(select(Partner).where(Partner.slug == slug))
    if partner is None:
        raise HTTPException(status_code=404, detail="Partner not found.")
    return partner


def _with_key(partner: Partner, key: str) -> PartnerKeyOut:
    return PartnerKeyOut(**PartnerOut.model_validate(partner).model_dump(), api_key=key)


@router.post("/admin/partners", response_model=PartnerKeyOut, status_code=201,
             dependencies=[Depends(require_admin)])
def create_partner(body: PartnerCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Partner).where(Partner.slug == body.slug)) is not None:
        raise HTTPException(status_code=409, detail="A partner with this slug already exists.")
    key, prefix, digest = issue_partner_key()
    partner = Partner(slug=body.slug, name=body.name, key_prefix=prefix, key_hash=digest,
                      rate_limit_per_minute=body.rate_limit_per_minute, is_active=True)
    db.add(partner)
    db.commit()
    db.refresh(partner)
    return _with_key(partner, key)


@router.get("/admin/partners", response_model=list[PartnerOut], dependencies=[Depends(require_admin)])
def list_partners(db: Session = Depends(get_db)):
    return db.scalars(select(Partner).order_by(Partner.created_at)).all()


@router.post("/admin/partners/{slug}/rotate-key", response_model=PartnerKeyOut,
             dependencies=[Depends(require_admin)])
def rotate_partner_key(slug: str, db: Session = Depends(get_db)):
    """The old key stops working at once; the partner's conversations stay theirs."""
    partner = _partner(db, slug)
    key, partner.key_prefix, partner.key_hash = issue_partner_key()
    partner.is_active = True
    db.commit()
    db.refresh(partner)
    return _with_key(partner, key)


@router.post("/admin/partners/{slug}/deactivate", response_model=PartnerOut,
             dependencies=[Depends(require_admin)])
def deactivate_partner(slug: str, db: Session = Depends(get_db)):
    partner = _partner(db, slug)
    partner.is_active = False
    db.commit()
    db.refresh(partner)
    return partner


# --- Partner server: conversations and messages -----------------------------

@router.post("/partner/conversations", response_model=PartnerConversationOut, status_code=201)
def create_partner_conversation(
    body: PartnerConversationCreate,
    partner: Partner = Depends(require_partner),
    user_id: str = Depends(require_partner_user),
    db: Session = Depends(get_db),
):
    conversation = UnifiedConversation(
        owner_partner_id=partner.id,
        owner_partner_user=partner_user_ref(partner, user_id),
        country_code="VN",
        destination_slug=body.destination_slug,
        preferred_mode="TRAVEL",
        locale=body.locale,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("/partner/conversations/{conversation_id}/messages", response_model=list[PartnerMessageOut])
def list_partner_messages(
    conversation_id: str,
    partner: Partner = Depends(require_partner),
    user_id: str = Depends(require_partner_user),
    db: Session = Depends(get_db),
):
    conversation = resolve_partner_conversation(db, conversation_id, partner, user_id)
    latest = db.scalars(
        select(UnifiedMessage)
        .where(UnifiedMessage.conversation_id == conversation.id)
        .order_by(UnifiedMessage.id.desc())
        .limit(MAX_HISTORY)
    ).all()
    return list(reversed(latest))


@router.delete("/partner/conversations/{conversation_id}", status_code=204)
def delete_partner_conversation(
    conversation_id: str,
    partner: Partner = Depends(require_partner),
    user_id: str = Depends(require_partner_user),
    db: Session = Depends(get_db),
):
    """Erase the conversation and everything recorded with it (ratings, consents)."""
    conversation = resolve_partner_conversation(db, conversation_id, partner, user_id)
    for model in (MessageFeedback, ConsentRecord, UnifiedMessage):
        db.execute(delete(model).where(model.conversation_id == conversation.id))
    db.delete(conversation)
    db.commit()
    return Response(status_code=204)


def _turn_payload(body: PartnerChatRequest) -> UnifiedChatRequest:
    # exclude_unset keeps "omitted" distinct from "null", which the planner
    # uses to keep or clear the conversation's locale and destination.
    data = body.model_dump(by_alias=True, exclude_unset=True)
    data["mode"] = "TRAVEL"
    return UnifiedChatRequest.model_validate(data)


async def _open_turn(conversation_id, body, partner, user_id, db):
    started = time.perf_counter()
    conversation = resolve_partner_conversation(db, conversation_id, partner, user_id)
    return await prepare_chat_turn(
        db, conversation, _turn_payload(body), verified_hotel=None, started=started, route="partner"
    )


@router.post("/partner/conversations/{conversation_id}/messages/stream")
async def stream_partner_message(
    conversation_id: str,
    body: PartnerChatRequest,
    partner: Partner = Depends(require_partner),
    user_id: str = Depends(require_partner_user),
    db: Session = Depends(get_db),
):
    """Server-sent events: token*/replace, optional action, then done (or error)."""
    stream, lease = await _open_turn(conversation_id, body, partner, user_id, db)
    try:
        return chat_stream_response(stream, lease)
    except BaseException:
        lease.release()
        raise


@router.post("/partner/conversations/{conversation_id}/messages", response_model=PartnerChatReply)
async def send_partner_message(
    conversation_id: str,
    body: PartnerChatRequest,
    partner: Partner = Depends(require_partner),
    user_id: str = Depends(require_partner_user),
    db: Session = Depends(get_db),
):
    """The same turn, answered once as JSON for partners that do not relay SSE."""
    stream, lease = await _open_turn(conversation_id, body, partner, user_id, db)
    reply, action, done, buffer = "", None, None, ""
    try:
        async for chunk in lease.release_after(stream):
            buffer += chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                event, data = None, {}
                for line in block.splitlines():
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                if event == "token":
                    reply += data.get("text", "")
                elif event == "replace":
                    reply = data.get("text", "")
                elif event == "action":
                    action = data
                elif event == "done":
                    done = data
                elif event == "error":
                    raise HTTPException(status_code=502, detail=data.get("detail") or "Chat failed.")
    finally:
        lease.release()
    if done is None:
        raise HTTPException(status_code=502, detail="Chat ended before completing.")
    return PartnerChatReply(
        conversation_id=done.get("conversation_id") or conversation_id,
        message_id=done.get("message_id"),
        reply=reply,
        suggestions=done.get("suggestions") or [],
        places=done.get("places") or [],
        action=action,
        artifact_id=done.get("artifact_id"),
        artifact_version=done.get("artifact_version"),
    )
