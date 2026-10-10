"""Unified Travel/Hotel MVP API. Legacy endpoints remain available separately."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import require_configured_admin
from app.models.unified import (
    ConsentRecord,
    HotelKnowledgeItem,
    HotelServiceItem,
    ServiceRequest,
    UnifiedConversation,
    utcnow,
)
from app.models.unified_schemas import (
    ConsentOut,
    ConsentUpdate,
    ConversationCreate,
    ConversationOut,
    FeedbackReasonsOut,
    FeedbackSummaryOut,
    HotelServiceItemOut,
    MessageFeedbackCreate,
    MessageFeedbackOut,
    ServiceRequestCreate,
    ServiceRequestOut,
    StayConnectRequest,
    RoomPinUnlockOut,
    StayConnectResponse,
    StayPinIssuedOut,
    StayVerifyRequest,
    StayVerifyResponse,
    UnifiedChatRequest,
)
from app.services import message_feedback as feedback_service
from app.services import place_cards
from app.services.demo_seed import demo_scenarios
from app.services.llm_client import (
    context_budget_for,
    llm_client,
    output_budget_for,
    requests_extended_output,
)
from app.services.chat_metrics import observe_sse_stream
from app.services.chat_capacity import CapacityStreamingResponse, chat_capacity
from app.services.auth import AuthContext, require_csrf
from app.services.conversation_access import (
    get_authorized_conversation,
    resolve_authorized_conversation,
)
from app.services.conversation_context import filter_answered_suggestions
from app.services.stay_access import (
    active_stay_out,
    clear_room_pin_lock,
    create_connection_challenge,
    issue_stay_pin,
    resolve_stay_token,
    verify_connection_challenge,
)
from app.services.unified_chat import build_chat_plan, persist_turn


router = APIRouter(prefix="/api/v1", tags=["unified-mvp"])
logger = logging.getLogger(__name__)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _service_request_out(request: ServiceRequest, item: HotelServiceItem) -> ServiceRequestOut:
    return ServiceRequestOut(
        id=request.id,
        service_item_id=request.service_item_id,
        service_name=item.name,
        quantity=request.quantity,
        note=request.note,
        status=request.status,
        total_amount=request.total_amount,
        currency=item.currency,
        created_at=request.created_at,
    )


# Catalogue media (D1/D4): the venue photos referenced by place cards.
# Allowlisted by name -- the requested name must match this pattern AND be a
# key in the currently loaded catalogue's media index -- and the resolved
# path is re-checked to stay inside that catalogue's media directory before
# the file is read, mirroring the containment check in
# app/services/source_storage.py (resolve, then require is_relative_to).
_MEDIA_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.svg$")


@router.get("/media/catalogue/{name}")
def catalogue_media(name: str):
    if not _MEDIA_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh.")
    state = place_cards.get_catalogue_state()
    resolved = state.media_index.get(name) if state else None
    if resolved is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh.")
    if not resolved.is_relative_to(state.media_root) or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh.")
    return Response(
        content=resolved.read_bytes(),
        media_type="image/svg+xml",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; sandbox",
            "Cache-Control": "public, max-age=86400",
        },
    )


@router.get("/demo/scenarios")
def list_demo_scenarios():
    if not settings.unified_demo_enabled or settings.app_env == "production":
        raise HTTPException(status_code=404, detail="Demo không được bật.")
    return {"enabled": True, "scenarios": demo_scenarios()}


@router.post("/stays/connect", response_model=StayConnectResponse)
def connect_stay(
    payload: StayConnectRequest,
    _: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    challenge, hotel = create_connection_challenge(db, payload.connect_code)
    return StayConnectResponse(
        connection_id=challenge.id,
        hotel_slug=hotel.slug,
        hotel_name=hotel.name,
        city=hotel.city,
        expires_at=challenge.expires_at,
    )


@router.post("/stays/verify", response_model=StayVerifyResponse)
def verify_stay(
    payload: StayVerifyRequest,
    _: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    raw_token, grant, stay, room, hotel = verify_connection_challenge(
        db, payload.connection_id, payload.pin
    )
    scopes = {scope.strip() for scope in grant.scopes.split(",") if scope.strip()}
    return StayVerifyResponse(
        access_token=raw_token,
        expires_at=grant.expires_at,
        stay=active_stay_out(grant, stay, room, hotel, scopes),
    )


@router.post(
    "/admin/hotels/{hotel_slug}/rooms/{room_number}/stay-pin",
    response_model=StayPinIssuedOut,
    dependencies=[Depends(require_configured_admin)],
)
def issue_room_stay_pin(
    hotel_slug: str,
    room_number: str,
    revoke_devices: bool = False,
    db: Session = Depends(get_db),
):
    """Reception: give the stay in progress its own PIN, shown only in this reply."""
    pin, stay, revoked = issue_stay_pin(db, hotel_slug, room_number, revoke_devices=revoke_devices)
    logger.info("Reception issued a stay PIN for %s room %s.", hotel_slug, room_number)
    return StayPinIssuedOut(
        room_number=room_number,
        stay_id=stay.id,
        pin=pin,
        valid_until=stay.check_out_at,
        revoked_devices=revoked,
    )


@router.post(
    "/admin/hotels/{hotel_slug}/rooms/{room_number}/pin-unlock",
    response_model=RoomPinUnlockOut,
    dependencies=[Depends(require_configured_admin)],
)
def unlock_room_pin(hotel_slug: str, room_number: str, db: Session = Depends(get_db)):
    """Reception: lift a PIN lockout after checking the guest in person."""
    room, closed = clear_room_pin_lock(db, hotel_slug, room_number)
    logger.info("Reception cleared the PIN lockout for %s room %s.", hotel_slug, room_number)
    return RoomPinUnlockOut(
        room_number=room.room_number,
        cleared_at=room.pin_lock_cleared_at,
        closed_challenges=closed,
    )


@router.get("/stays/active")
def get_active_stay(
    x_stay_token: str | None = Header(default=None, alias="X-Stay-Token"),
    db: Session = Depends(get_db),
):
    resolved = resolve_stay_token(db, x_stay_token, optional=True)
    if resolved is None:
        return {"active": False, "stay": None}
    grant, stay, room, hotel, scopes = resolved
    return {"active": True, "stay": active_stay_out(grant, stay, room, hotel, scopes)}


@router.delete("/stays/active", status_code=204)
def disconnect_active_stay(
    x_stay_token: str | None = Header(default=None, alias="X-Stay-Token"),
    db: Session = Depends(get_db),
):
    grant, _, _, _, _ = resolve_stay_token(db, x_stay_token)
    grant.revoked_at = utcnow()
    db.commit()
    return None


@router.post("/conversations", response_model=ConversationOut)
def create_conversation(
    payload: ConversationCreate,
    x_stay_token: str | None = Header(default=None, alias="X-Stay-Token"),
    auth_context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    resolved = resolve_stay_token(db, x_stay_token, optional=True)
    conversation = UnifiedConversation(
        owner_user_id=auth_context.user_id,
        owner_session_id=None if auth_context.user_id else auth_context.session.id,
        country_code=payload.country_code.upper(),
        destination_slug=payload.destination_slug,
        active_stay_id=resolved[1].id if resolved else None,
        preferred_mode="HOTEL" if resolved and payload.preferred_mode == "HOTEL" else "TRAVEL",
        locale=payload.locale,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def get_conversation(
    conversation: UnifiedConversation = Depends(get_authorized_conversation),
):
    return conversation


@router.get("/hotel/services", response_model=list[HotelServiceItemOut])
def list_hotel_services(
    x_stay_token: str | None = Header(default=None, alias="X-Stay-Token"),
    db: Session = Depends(get_db),
):
    _, stay, _, _, _ = resolve_stay_token(db, x_stay_token, required_scope="hotel:read")
    return db.query(HotelServiceItem).filter(
        HotelServiceItem.hotel_id == stay.hotel_id,
        HotelServiceItem.is_active.is_(True),
    ).order_by(HotelServiceItem.category, HotelServiceItem.name).all()


@router.get("/hotel/nearby")
def list_nearby_places(
    x_stay_token: str | None = Header(default=None, alias="X-Stay-Token"),
    db: Session = Depends(get_db),
):
    resolved = resolve_stay_token(db, x_stay_token, optional=True)
    if resolved is None:
        return []
    _, stay, _, _, _ = resolved
    rows = db.query(HotelKnowledgeItem).filter(
        HotelKnowledgeItem.hotel_id == stay.hotel_id,
        HotelKnowledgeItem.category == "travel",
        HotelKnowledgeItem.visibility == "PUBLIC",
        HotelKnowledgeItem.is_active.is_(True),
    ).order_by(HotelKnowledgeItem.sort_order, HotelKnowledgeItem.title).all()
    return [{"id": row.id, "title": row.title, "content": row.content} for row in rows]


@router.post("/service-requests", response_model=ServiceRequestOut)
def create_service_request(
    payload: ServiceRequestCreate,
    x_stay_token: str | None = Header(default=None, alias="X-Stay-Token"),
    db: Session = Depends(get_db),
):
    _, stay, _, _, _ = resolve_stay_token(
        db, x_stay_token, required_scope="service:create"
    )
    existing = db.query(ServiceRequest).filter(
        ServiceRequest.stay_id == stay.id,
        ServiceRequest.idempotency_key == payload.idempotency_key,
    ).first()
    if existing is not None:
        existing_item = db.get(HotelServiceItem, existing.service_item_id)
        assert existing_item is not None
        return _service_request_out(existing, existing_item)

    item = db.get(HotelServiceItem, payload.service_item_id)
    if item is None or not item.is_active or item.hotel_id != stay.hotel_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy dịch vụ.")
    if item.requires_confirmation and not payload.confirmed:
        raise HTTPException(
            status_code=409,
            detail="Dịch vụ cần được khách xác nhận trước khi gửi.",
        )
    request = ServiceRequest(
        hotel_id=stay.hotel_id,
        stay_id=stay.id,
        service_item_id=item.id,
        quantity=payload.quantity,
        note=payload.note.strip(),
        total_amount=item.price * payload.quantity,
        idempotency_key=payload.idempotency_key,
        status="SUBMITTED",
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return _service_request_out(request, item)


@router.get("/service-requests", response_model=list[ServiceRequestOut])
def list_service_requests(
    x_stay_token: str | None = Header(default=None, alias="X-Stay-Token"),
    db: Session = Depends(get_db),
):
    _, stay, _, _, _ = resolve_stay_token(
        db, x_stay_token, required_scope="service:read"
    )
    requests = db.query(ServiceRequest).filter(
        ServiceRequest.stay_id == stay.id
    ).order_by(ServiceRequest.created_at.desc()).limit(50).all()
    items = {
        item.id: item
        for item in db.query(HotelServiceItem).filter(
            HotelServiceItem.id.in_({request.service_item_id for request in requests})
        ).all()
    } if requests else {}
    return [_service_request_out(request, items[request.service_item_id]) for request in requests]


@router.get("/conversations/{conversation_id}/consents", response_model=list[ConsentOut])
def list_consents(
    conversation: UnifiedConversation = Depends(get_authorized_conversation),
    db: Session = Depends(get_db),
):
    return db.query(ConsentRecord).filter(
        ConsentRecord.conversation_id == conversation.id
    ).order_by(ConsentRecord.purpose).all()


@router.put("/conversations/{conversation_id}/consents", response_model=ConsentOut)
def update_consent(
    payload: ConsentUpdate,
    conversation: UnifiedConversation = Depends(get_authorized_conversation),
    _: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    record = db.query(ConsentRecord).filter(
        ConsentRecord.conversation_id == conversation.id,
        ConsentRecord.purpose == payload.purpose,
    ).first()
    now = utcnow()
    if record is None:
        record = ConsentRecord(
            conversation_id=conversation.id,
            purpose=payload.purpose,
            policy_version=settings.privacy_policy_version,
        )
        db.add(record)
    record.status = payload.status
    record.policy_version = settings.privacy_policy_version
    record.recorded_at = now
    record.withdrawn_at = now if payload.status == "WITHDRAWN" else None
    db.commit()
    db.refresh(record)
    return record


# --- Answer quality feedback -------------------------------------------------
# A rating on one assistant turn. No free text is accepted anywhere here; see
# app/services/message_feedback.py for why.
#
# The conversation-scoped routes go through the same ownership boundary as the
# consent routes, so a rating cannot be written against somebody else's
# conversation, and the writes carry CSRF like every other state change.


@router.get("/feedback/reasons", response_model=FeedbackReasonsOut)
def list_feedback_reasons():
    """The reason codes the front end may offer, so the lists cannot drift."""
    return feedback_service.available_reasons()


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=MessageFeedbackOut | None,
)
def read_message_feedback(
    message_id: int,
    conversation: UnifiedConversation = Depends(get_authorized_conversation),
    db: Session = Depends(get_db),
):
    try:
        record = feedback_service.get_feedback(
            db, conversation_id=conversation.id, message_id=message_id
        )
    except feedback_service.FeedbackError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return record


@router.put(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=MessageFeedbackOut,
)
def set_message_feedback(
    message_id: int,
    payload: MessageFeedbackCreate,
    conversation: UnifiedConversation = Depends(get_authorized_conversation),
    _: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    """Record or replace the rating for one answer.

    PUT rather than POST: rating the same answer twice is a correction, not a
    second opinion, and the table keeps one row per message.
    """
    try:
        record = feedback_service.record_feedback(
            db,
            conversation_id=conversation.id,
            message_id=message_id,
            rating=payload.rating,
            reason=payload.reason,
        )
    except feedback_service.FeedbackError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return record


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    status_code=204,
)
def clear_message_feedback(
    message_id: int,
    conversation: UnifiedConversation = Depends(get_authorized_conversation),
    _: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    """Withdraw a rating. Idempotent: no row to remove is still a 204."""
    try:
        feedback_service.clear_feedback(
            db, conversation_id=conversation.id, message_id=message_id
        )
    except feedback_service.FeedbackError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return None


@router.get(
    "/feedback/summary",
    response_model=FeedbackSummaryOut,
    dependencies=[Depends(require_configured_admin)],
)
def feedback_summary(
    context_type: str | None = None,
    db: Session = Depends(get_db),
):
    """Aggregate counts, for whoever has to decide what to fix next.

    Admin-gated and fail-closed: without a configured ADMIN_API_KEY this is a
    404, because it reports on guest activity even though it returns no guest
    text. A thumbs-down button nobody can read is decoration, so the counting
    ships with the button rather than waiting for a dashboard.
    """
    return feedback_service.summarise(db, context_type=context_type)


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: str,
    payload: UnifiedChatRequest,
    x_stay_token: str | None = Header(default=None, alias="X-Stay-Token"),
    db: Session = Depends(get_db),
    auth_context: AuthContext = Depends(require_csrf),
):
    started = time.perf_counter()
    # Evaluation helpers call the route function directly with an explicit DB
    # session. HTTP requests always receive a real AuthContext and therefore
    # pass through the ownership boundary.
    if isinstance(auth_context, AuthContext):
        conversation = resolve_authorized_conversation(
            db,
            conversation_id,
            auth_context,
        )
    else:
        conversation = db.get(UnifiedConversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại.")
    resolved = resolve_stay_token(db, x_stay_token, optional=True)
    verified_hotel = resolved[3] if resolved else None
    if payload.mode == "HOTEL":
        if resolved is None:
            raise HTTPException(status_code=403, detail="Hotel mode cần xác minh lưu trú.")
        conversation.active_stay_id = resolved[1].id
    stream, capacity_lease = await prepare_chat_turn(
        db, conversation, payload, verified_hotel=verified_hotel, started=started
    )
    try:
        return chat_stream_response(stream, capacity_lease)
    except BaseException:
        capacity_lease.release()
        raise


async def prepare_chat_turn(
    db: Session,
    conversation: UnifiedConversation,
    payload: UnifiedChatRequest,
    *,
    verified_hotel,
    started: float,
    route: str = "unified",
):
    """Plan one turn for an already-authorized conversation.

    Returns the SSE event stream and the chat-capacity lease it holds. Shared
    by the PWA route above and the partner API (app/api/partner.py), so both
    answer through exactly the same planning, persistence and event shape.
    The caller must hand the lease to a CapacityStreamingResponse or release
    it itself.
    """
    capacity_lease = chat_capacity.try_acquire()
    try:
        # Omission preserves an existing editorial scope; an explicit JSON null
        # clears it so country-wide Travel questions are not trapped in a stale
        # destination selected by an older client.
        if "destination_slug" in payload.model_fields_set:
            conversation.destination_slug = payload.destination_slug
        conversation.preferred_mode = payload.mode
        # Keep the conversation's selected language when older clients omit the
        # optional locale field. An explicitly supplied locale still applies to
        # this turn and future turns.
        if "locale" in payload.model_fields_set:
            conversation.locale = payload.locale
        db.commit()

        plan = await build_chat_plan(
            db,
            conversation,
            payload.message,
            payload.mode,
            verified_hotel=verified_hotel,
            current_artifact=payload.current_artifact,
        )
    except BaseException:
        capacity_lease.release()
        raise
    try:
        prepared = time.perf_counter()
        # Card selection is a separate, deterministic lookup over the catalogue
        # (destination + intent keywords + attributes): it never reads or feeds
        # plan.llm_messages, so it cannot change what the model was asked or told.
        # Skipped when the turn already carries an action (a service draft or a
        # GENERATIVE_UI artifact) so cards never compete with that response.
        places = (
            place_cards.select_place_cards(
                payload.message,
                mode=payload.mode,
                destination_hint=conversation.destination_slug,
                locale=conversation.locale,
            )
            if payload.mode == "TRAVEL" and plan.action is None
            else []
        )
        output_budget = output_budget_for(
            plan.llm_messages,
            long_request=requests_extended_output(payload.message),
        )
        context_budget = context_budget_for(
            plan.llm_messages,
            long_request=requests_extended_output(payload.message),
        )
    except BaseException:
        capacity_lease.release()
        raise

    async def event_generator() -> AsyncGenerator[str, None]:
        reply = ""
        try:
            if plan.deterministic_reply is not None:
                reply = plan.deterministic_reply
                yield _sse("replace", {"text": reply})
            else:
                provider_stream = llm_client.chat_stream(
                    plan.llm_messages,
                    num_predict=output_budget,
                    num_ctx=context_budget,
                )
                try:
                    async for part in provider_stream:
                        reply += part
                        yield _sse("token", {"text": part})
                finally:
                    await provider_stream.aclose()
            if plan.action:
                yield _sse("action", plan.action)
            persisted_id = persist_turn(
                conversation.id, payload.mode, payload.message, reply
            )
            # Only a real row id travels to the client. The answer has already
            # been streamed at this point, so a persistence problem must cost
            # the rating button, never the reply.
            message_id = (
                persisted_id
                if isinstance(persisted_id, int) and not isinstance(persisted_id, bool)
                else None
            )
            final_suggestions = filter_answered_suggestions(
                plan.suggestions, reply
            )
            yield _sse(
                "done",
                {
                    "conversation_id": conversation.id,
                    "resolved_context": payload.mode,
                    "suggestions": final_suggestions,
                    # What the client rates. None when the conversation was
                    # deleted mid-stream; the client then simply offers no
                    # rating rather than posting against a guessed id.
                    "message_id": message_id,
                    **(
                        {
                            "artifact_id": plan.action["artifact"]["id"],
                            "artifact_version": plan.action["artifact"]["version"],
                        }
                        if plan.action
                        and plan.action.get("type") == "GENERATIVE_UI"
                        and plan.action.get("artifact")
                        else {}
                    ),
                    **(
                        {
                            "artifact_id": plan.action["artifactId"],
                            "artifact_version": plan.action["baseVersion"] + 1,
                        }
                        if plan.action
                        and plan.action.get("type") == "GENERATIVE_UI"
                        and plan.action.get("artifactId")
                        else {}
                    ),
                    # Optional and additive: only present in TRAVEL mode, and
                    # only when the catalogue actually matched something, so
                    # an older client that ignores unknown fields sees no
                    # difference.
                    **({"places": places} if places else {}),
                },
            )
        except Exception as exc:  # noqa: BLE001
            # The detail reaches partner servers too, so the exception text
            # stays in the server log.
            logger.warning("Chat turn failed mid-stream: %s", type(exc).__name__, exc_info=True)
            yield _sse("error", {"detail": "Không thể hoàn tất câu trả lời."})

    try:
        retrieved_chunk_count = len(plan.sources)
        # The stream can outlive all request-scoped reads. persist_turn() opens a
        # short-lived session itself, so this connection is no longer needed.
        db.close()
        stream = observe_sse_stream(
            event_generator(),
            started=started,
            prepared=prepared,
            scope=payload.mode,
            route=route,
            question=payload.message,
            session_id=conversation.id,
            retrieved_chunk_count=retrieved_chunk_count,
        )
    except BaseException:
        capacity_lease.release()
        raise
    return stream, capacity_lease


def chat_stream_response(stream, lease) -> CapacityStreamingResponse:
    return CapacityStreamingResponse(
        stream,
        lease=lease,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
