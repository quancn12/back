"""Pydantic schemas for API requests and responses."""
from datetime import datetime, time
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class HotelCreate(BaseModel):
    slug: str
    name: str
    city: str | None = None
    checkin_time: time | None = None
    checkout_time: time | None = None
    default_language: str = "vi"
    supported_languages: str = "vi,en"
    latitude: float | None = None
    longitude: float | None = None


class HotelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    city: str | None
    checkin_time: time | None
    checkout_time: time | None
    default_language: str
    supported_languages: str
    latitude: float | None
    longitude: float | None
    created_at: datetime


class HotelDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    hotel_id: str
    filename: str
    content_hash: str | None
    status: str
    error_message: str | None
    chunk_count: int
    uploaded_at: datetime
    processed_at: datetime | None


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatSource(BaseModel):
    text: str
    distance: float
    source: str | None = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    sources: list[ChatSource]


class DestinationCreate(BaseModel):
    slug: str
    name: str
    destination_type: str = "region"
    country_code: str = "VN"
    country_name: str = "Việt Nam"
    province_name: str | None = None
    parent_id: str | None = None
    aliases: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class DestinationOut(DestinationCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


class TravelDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    primary_destination_id: str
    filename: str
    source_path: str | None
    content_hash: str
    document_type: str
    tour_code: str | None
    duration_days: float | None
    locations: str | None
    status: str
    error_message: str | None
    chunk_count: int
    embedding_model: str | None
    chunker_version: str | None
    source_modified_at: datetime | None
    uploaded_at: datetime
    processed_at: datetime | None


class DocumentVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_document_id: str
    version: int
    content_hash: str
    status: str
    is_active: bool
    error_message: str | None
    chunk_count: int
    embedding_model: str
    chunker_version: str
    created_at: datetime
    processed_at: datetime | None


class SourceDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_key: str
    primary_destination_id: str
    filename: str
    status: str
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IngestionJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_document_id: str
    document_version_id: str
    status: str
    attempts: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class EnqueueDocumentResponse(BaseModel):
    changed: bool
    source: SourceDocumentOut
    version: DocumentVersionOut
    job: IngestionJobOut | None


class TravelChatRequest(BaseModel):
    message: str = Field(min_length=1)
    # Send back the id of the previous answer to continue that conversation.
    # `conversation_id` and the older name `session_id` are the same value;
    # either may be sent.
    session_id: str | None = None
    conversation_id: str | None = None
    destination_slug: str | None = None
    country_code: str = "VN"

    @model_validator(mode="after")
    def _one_conversation(self) -> "TravelChatRequest":
        if self.conversation_id and self.session_id and self.conversation_id != self.session_id:
            raise ValueError("conversation_id and session_id name the same conversation; send one, or the same value.")
        self.session_id = self.conversation_id or self.session_id
        self.conversation_id = self.session_id
        return self


class TravelChatSource(BaseModel):
    text: str
    distance: float
    source: str | None = None
    destination_slug: str | None = None
    locations: str | None = None
    section: str | None = None


class TravelChatResponse(BaseModel):
    reply: str
    session_id: str
    country_code: str = "VN"
    destination_slug: str | None = None
    sources: list[TravelChatSource]

    @computed_field
    @property
    def conversation_id(self) -> str:
        """Same as `session_id`: send it back to continue the conversation."""
        return self.session_id
