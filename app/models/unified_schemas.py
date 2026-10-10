"""HTTP contracts for the unified VietSage MVP."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.generative_ui import (
    ArtifactPatch,
    StructuredPlanArtifact,
    TravelItineraryArtifact,
    TravelItineraryDraft,
    TravelPatchDraft,
)


class StayConnectRequest(BaseModel):
    connect_code: str = Field(min_length=4, max_length=200)


class StayConnectResponse(BaseModel):
    connection_id: str
    hotel_slug: str
    hotel_name: str
    city: str | None
    verification_required: bool = True
    expires_at: datetime


class StayVerifyRequest(BaseModel):
    connection_id: str
    pin: str = Field(min_length=4, max_length=12)


class ActiveStayOut(BaseModel):
    id: str
    hotel_slug: str
    hotel_name: str
    room_number: str
    guest_alias: str
    check_in_at: datetime
    check_out_at: datetime
    scopes: list[str]


class StayVerifyResponse(BaseModel):
    access_token: str
    expires_at: datetime
    stay: ActiveStayOut


class StayPinIssuedOut(BaseModel):
    """Returned once to reception; only the hash is stored."""

    room_number: str
    stay_id: str
    pin: str
    valid_until: datetime
    revoked_devices: int


class RoomPinUnlockOut(BaseModel):
    room_number: str
    cleared_at: datetime
    closed_challenges: int


class ConversationCreate(BaseModel):
    country_code: str = "VN"
    destination_slug: str | None = None
    preferred_mode: Literal["TRAVEL", "HOTEL"] = "TRAVEL"
    locale: str = "vi"


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    country_code: str
    destination_slug: str | None
    active_stay_id: str | None
    preferred_mode: str
    locale: str


class UnifiedChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    mode: Literal["TRAVEL", "HOTEL"] = "TRAVEL"
    destination_slug: str | None = None
    locale: Literal["vi", "en", "zh-Hans"] = "vi"
    # The current artifact is client-provided context for a bounded edit only.
    # It is never treated as authorization and is validated before prompting
    # the model. The field is optional for ordinary text turns.
    current_artifact: TravelItineraryArtifact | StructuredPlanArtifact | None = Field(
        default=None,
        alias="currentArtifact",
    )


class HotelServiceItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    category: str
    name: str
    description: str
    price: int
    currency: str
    hours: str
    estimated_minutes: int | None
    requires_confirmation: bool


class ServiceRequestCreate(BaseModel):
    service_item_id: str
    quantity: int = Field(default=1, ge=1, le=20)
    note: str = Field(default="", max_length=1000)
    confirmed: bool = False
    idempotency_key: str = Field(min_length=8, max_length=120)


class ServiceRequestOut(BaseModel):
    id: str
    service_item_id: str
    service_name: str
    quantity: int
    note: str
    status: str
    total_amount: int
    currency: str
    created_at: datetime


class ConsentUpdate(BaseModel):
    purpose: Literal[
        "PERSONALIZED_RECOMMENDATIONS",
        "PRECISE_LOCATION",
        "USAGE_ANALYTICS",
        "CHAT_HISTORY_PERSONALIZATION",
        "PUSH_NOTIFICATIONS",
    ]
    status: Literal["GRANTED", "DENIED", "WITHDRAWN"]


class ConsentOut(BaseModel):
    purpose: str
    status: str
    policy_version: str
    recorded_at: datetime
    withdrawn_at: datetime | None



class MessageFeedbackCreate(BaseModel):
    """A rating on one assistant answer.

    No free-text field, on purpose: see app/services/message_feedback.py. The
    reason list is validated in the service against the rating, so an invalid
    pair is a 400 rather than a row that quietly skews the counts.
    """

    rating: Literal["up", "down"]
    reason: str | None = Field(default=None, max_length=32)


class MessageFeedbackOut(BaseModel):
    message_id: int
    rating: str
    reason: str | None
    context_type: str
    locale: str
    created_at: datetime
    updated_at: datetime


class FeedbackReasonsOut(BaseModel):
    """What the front end may offer, so the two lists cannot drift apart."""

    up: list[str]
    down: list[str]


class FeedbackSummaryOut(BaseModel):
    total: int
    up: int
    down: int
    # None when nothing has been rated. Reporting an unmeasured system as 0%
    # satisfaction would be a lie that looks like data.
    satisfaction: float | None
    reasons: dict[str, dict[str, int]]
    context_type: str | None
