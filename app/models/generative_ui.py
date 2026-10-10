"""Validated contracts for controlled Hybrid Generative UI responses.

Travel itineraries and bounded generic study/schedule plans use separate
contracts. The models are also used for inbound ``currentArtifact`` payloads
so the server never needs to trust an unbounded client-side artifact while
asking the model for a patch.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_PLAN_PERIODS = 90
MAX_ITEMS_PER_PERIOD = 50
MAX_TOTAL_PLAN_ITEMS = 300
_SAFE_MAP_HOSTS = {
    "maps.google.com",
    "www.google.com",
    "maps.apple.com",
    "www.openstreetmap.org",
    "openstreetmap.org",
}
_ACTION_COMMANDS = {
    "reduce_pace",
    "add_food",
    "reduce_cost",
    "edit_day",
    "open_map",
}
_MAX_ARTIFACT_TEXT = 50_000
_SAFE_PATH_PATTERN = re.compile(
    r"^/(?:title|destination|summary|days/[A-Za-z0-9][A-Za-z0-9_-]{0,63}/items(?:/[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?:/(?:time|title|place|description|duration|coordinates|mapUrl))?)?)$"
)
_ITEM_PATH_PATTERN = re.compile(
    r"^/days/(?P<day>[A-Za-z0-9][A-Za-z0-9_-]{0,63})/items/(?P<item>[A-Za-z0-9][A-Za-z0-9_-]{0,63})$"
)
_ITEMS_PATH_PATTERN = re.compile(
    r"^/days/(?P<day>[A-Za-z0-9][A-Za-z0-9_-]{0,63})/items$"
)
_FIELD_PATH_PATTERN = re.compile(
    r"^/days/(?P<day>[A-Za-z0-9][A-Za-z0-9_-]{0,63})/items/(?P<item>[A-Za-z0-9][A-Za-z0-9_-]{0,63})/(?P<field>time|title|place|description|duration|coordinates|mapUrl)$"
)
_PLAN_SAFE_PATH_PATTERN = re.compile(
    r"^/(?:title|summary|periods/[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?:/(?:label|date|items(?:/[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?:/(?:time|title|description|duration))?)?)?)?)$"
)
_PLAN_ITEM_PATH_PATTERN = re.compile(
    r"^/periods/(?P<period>[A-Za-z0-9][A-Za-z0-9_-]{0,63})/items/(?P<item>[A-Za-z0-9][A-Za-z0-9_-]{0,63})$"
)
_PLAN_ITEMS_PATH_PATTERN = re.compile(
    r"^/periods/(?P<period>[A-Za-z0-9][A-Za-z0-9_-]{0,63})/items$"
)
_PLAN_FIELD_PATH_PATTERN = re.compile(
    r"^/periods/(?P<period>[A-Za-z0-9][A-Za-z0-9_-]{0,63})/items/(?P<item>[A-Za-z0-9][A-Za-z0-9_-]{0,63})/(?P<field>time|title|description|duration)$"
)


def _validate_id(value: str) -> str:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError("ID must contain only letters, numbers, '_' or '-'.")
    return value


class GenerativeUIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Coordinates(GenerativeUIModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class TripItem(GenerativeUIModel):
    id: str = Field(min_length=1, max_length=64)
    time: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=200)
    place: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    duration: str | None = Field(default=None, max_length=80)
    coordinates: Coordinates | None = None
    map_url: str | None = Field(default=None, alias="mapUrl", max_length=500)

    _id = field_validator("id")(_validate_id)

    @field_validator("map_url")
    @classmethod
    def validate_map_url(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in _SAFE_MAP_HOSTS:
                raise ValueError("mapUrl host is not supported.")
        return value


class TripDay(GenerativeUIModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    date: str | None = Field(default=None, max_length=32)
    items: list[TripItem] = Field(min_length=1, max_length=MAX_ITEMS_PER_PERIOD)

    _id = field_validator("id")(_validate_id)


class ArtifactAction(GenerativeUIModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    command: str = Field(min_length=1, max_length=120)

    _id = field_validator("id")(_validate_id)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        if value not in _ACTION_COMMANDS:
            raise ValueError("Suggested action command is not supported.")
        return value


class TravelItineraryArtifact(GenerativeUIModel):
    id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1, le=2_000_000_000)
    context: Literal["travel_itinerary"] = "travel_itinerary"
    response_mode: Literal["artifact"] = Field(default="artifact", alias="responseMode")
    title: str = Field(min_length=1, max_length=200)
    destination: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=1000)
    # Ninety periods cover multi-week plans while keeping the artifact bounded.
    # Requests beyond this explicit limit use the readable text path.
    days: list[TripDay] = Field(min_length=1, max_length=MAX_PLAN_PERIODS)
    layout: Literal["timeline_map"] = "timeline_map"
    actions: list[ArtifactAction] = Field(default_factory=list, max_length=8)

    _id = field_validator("id")(_validate_id)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "TravelItineraryArtifact":
        day_ids = [day.id for day in self.days]
        if len(day_ids) != len(set(day_ids)):
            raise ValueError("Day IDs must be unique.")
        item_ids: list[str] = []
        for day in self.days:
            item_ids.extend(item.id for item in day.items)
        if len(item_ids) > MAX_TOTAL_PLAN_ITEMS:
            raise ValueError("Artifact item count exceeds the bounded plan limit.")
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("Item IDs must be unique across the artifact.")
        action_ids = [action.id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("Action IDs must be unique.")
        total_text = len(self.title) + len(self.destination or "") + len(self.summary or "")
        total_text += sum(
            len(day.label)
            + len(day.date or "")
            + sum(
                len(item.time)
                + len(item.title)
                + len(item.place)
                + len(item.description or "")
                + len(item.duration or "")
                + len(item.map_url or "")
                for item in day.items
            )
            for day in self.days
        )
        if total_text > _MAX_ARTIFACT_TEXT:
            raise ValueError("Artifact text exceeds the bounded context limit.")
        return self


class TripItemDraft(GenerativeUIModel):
    """Structured model output before server-assigned stable IDs."""

    id: str | None = Field(default=None, max_length=64)
    time: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=200)
    place: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    duration: str | None = Field(default=None, max_length=80)
    coordinates: Coordinates | None = None
    map_url: str | None = Field(default=None, alias="mapUrl", max_length=500)

    @field_validator("id")
    @classmethod
    def validate_optional_id(cls, value: str | None) -> str | None:
        return _validate_id(value) if value is not None else None

    @field_validator("map_url")
    @classmethod
    def validate_map_url(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in _SAFE_MAP_HOSTS:
                raise ValueError("mapUrl host is not supported.")
        return value


class TripDayDraft(GenerativeUIModel):
    id: str | None = Field(default=None, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    date: str | None = Field(default=None, max_length=32)
    items: list[TripItemDraft] = Field(min_length=1, max_length=MAX_ITEMS_PER_PERIOD)

    @field_validator("id")
    @classmethod
    def validate_optional_id(cls, value: str | None) -> str | None:
        return _validate_id(value) if value is not None else None


class TravelItineraryDraft(GenerativeUIModel):
    title: str = Field(min_length=1, max_length=200)
    destination: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=1000)
    days: list[TripDayDraft] = Field(min_length=1, max_length=MAX_PLAN_PERIODS)
    layout: Literal["timeline_map"] = "timeline_map"
    actions: list[ArtifactAction] = Field(default_factory=list, max_length=8)


class PlanItem(GenerativeUIModel):
    """Domain-neutral timeline item used by non-travel plan artifacts."""

    id: str = Field(min_length=1, max_length=64)
    time: str | None = Field(default=None, max_length=32)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    duration: str | None = Field(default=None, max_length=80)

    _id = field_validator("id")(_validate_id)


class PlanPeriod(GenerativeUIModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    date: str | None = Field(default=None, max_length=32)
    items: list[PlanItem] = Field(min_length=1, max_length=MAX_ITEMS_PER_PERIOD)

    _id = field_validator("id")(_validate_id)


class StructuredPlanArtifact(GenerativeUIModel):
    """Bounded generic plan envelope for study and schedule contexts."""

    id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1, le=2_000_000_000)
    context: Literal["study_plan", "schedule"]
    response_mode: Literal["artifact"] = Field(default="artifact", alias="responseMode")
    title: str = Field(min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=1000)
    periods: list[PlanPeriod] = Field(min_length=1, max_length=MAX_PLAN_PERIODS)
    layout: Literal["timeline"] = "timeline"

    _id = field_validator("id")(_validate_id)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "StructuredPlanArtifact":
        period_ids = [period.id for period in self.periods]
        if len(period_ids) != len(set(period_ids)):
            raise ValueError("Period IDs must be unique.")
        item_ids = [item.id for period in self.periods for item in period.items]
        if len(item_ids) > MAX_TOTAL_PLAN_ITEMS:
            raise ValueError("Artifact item count exceeds the bounded plan limit.")
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("Item IDs must be unique across the artifact.")
        total_text = len(self.title) + len(self.summary or "")
        total_text += sum(
            len(period.label)
            + len(period.date or "")
            + sum(
                len(item.time or "")
                + len(item.title)
                + len(item.description or "")
                + len(item.duration or "")
                for item in period.items
            )
            for period in self.periods
        )
        if total_text > _MAX_ARTIFACT_TEXT:
            raise ValueError("Artifact text exceeds the bounded context limit.")
        return self


class PlanItemDraft(GenerativeUIModel):
    id: str | None = Field(default=None, max_length=64)
    time: str | None = Field(default=None, max_length=32)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    duration: str | None = Field(default=None, max_length=80)

    @field_validator("id")
    @classmethod
    def validate_optional_id(cls, value: str | None) -> str | None:
        return _validate_id(value) if value is not None else None


class PlanPeriodDraft(GenerativeUIModel):
    id: str | None = Field(default=None, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    date: str | None = Field(default=None, max_length=32)
    items: list[PlanItemDraft] = Field(min_length=1, max_length=MAX_ITEMS_PER_PERIOD)

    @field_validator("id")
    @classmethod
    def validate_optional_id(cls, value: str | None) -> str | None:
        return _validate_id(value) if value is not None else None


class StructuredPlanDraft(GenerativeUIModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=1000)
    periods: list[PlanPeriodDraft] = Field(min_length=1, max_length=MAX_PLAN_PERIODS)
    layout: Literal["timeline"] = "timeline"


PatchOperation = Literal["replace", "add", "remove", "move"]


class ArtifactPatch(GenerativeUIModel):
    op: PatchOperation
    path: str = Field(min_length=1, max_length=250)
    value: object | None = None
    to_day_id: str | None = Field(default=None, alias="toDayId")
    to_index: int | None = Field(default=None, alias="toIndex", ge=0, le=MAX_ITEMS_PER_PERIOD)

    @field_validator("path")
    @classmethod
    def validate_safe_path(cls, value: str) -> str:
        if not _SAFE_PATH_PATTERN.fullmatch(value):
            raise ValueError("Patch path is not supported.")
        return value

    @field_validator("to_day_id")
    @classmethod
    def validate_optional_day_id(cls, value: str | None) -> str | None:
        return _validate_id(value) if value is not None else None

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "ArtifactPatch":
        item_path = _ITEM_PATH_PATTERN.fullmatch(self.path)
        items_path = _ITEMS_PATH_PATTERN.fullmatch(self.path)
        field_path = _FIELD_PATH_PATTERN.fullmatch(self.path)
        if self.op == "add":
            if not items_path and not item_path:
                raise ValueError("Add patches target a day item collection or item ID.")
            if not isinstance(self.value, dict):
                raise ValueError("Add patches require an item object.")
        elif self.op in {"remove", "move"}:
            if not item_path:
                raise ValueError("Remove and move patches target an item ID.")
            if self.op == "move" and self.to_day_id is None:
                raise ValueError("Move patches require toDayId.")
        elif self.op == "replace":
            if not (field_path or item_path or self.path in {"/title", "/destination", "/summary"}):
                raise ValueError("Replace target is not supported.")
            if self.value is None:
                raise ValueError("Replace patches require a value.")
        return self


class PlanArtifactPatch(GenerativeUIModel):
    """ID-addressed patch vocabulary for generic period-based plans."""

    op: PatchOperation
    path: str = Field(min_length=1, max_length=250)
    value: object | None = None
    to_period_id: str | None = Field(default=None, alias="toPeriodId")
    to_index: int | None = Field(default=None, alias="toIndex", ge=0, le=MAX_ITEMS_PER_PERIOD)

    @field_validator("path")
    @classmethod
    def validate_safe_path(cls, value: str) -> str:
        if not _PLAN_SAFE_PATH_PATTERN.fullmatch(value):
            raise ValueError("Plan patch path is not supported.")
        return value

    @field_validator("to_period_id")
    @classmethod
    def validate_optional_period_id(cls, value: str | None) -> str | None:
        return _validate_id(value) if value is not None else None

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "PlanArtifactPatch":
        item_path = _PLAN_ITEM_PATH_PATTERN.fullmatch(self.path)
        items_path = _PLAN_ITEMS_PATH_PATTERN.fullmatch(self.path)
        field_path = _PLAN_FIELD_PATH_PATTERN.fullmatch(self.path)
        if self.op == "add":
            if not items_path and not item_path:
                raise ValueError("Plan add patches target a period item collection or item ID.")
            if not isinstance(self.value, dict):
                raise ValueError("Plan add patches require an item object.")
        elif self.op in {"remove", "move"}:
            if not item_path:
                raise ValueError("Plan remove and move patches target an item ID.")
            if self.op == "move" and self.to_period_id is None:
                raise ValueError("Plan move patches require toPeriodId.")
        elif self.op == "replace":
            if not (field_path or item_path or self.path in {"/title", "/summary"}):
                raise ValueError("Plan replace target is not supported.")
            if self.value is None:
                raise ValueError("Plan replace patches require a value.")
        return self


class TravelPatchDraft(GenerativeUIModel):
    patches: list[ArtifactPatch] = Field(min_length=1, max_length=20)
    explanation: str | None = Field(default=None, max_length=500)


class PlanPatchDraft(GenerativeUIModel):
    patches: list[PlanArtifactPatch] = Field(min_length=1, max_length=20)
    explanation: str | None = Field(default=None, max_length=500)


def artifact_dump(
    artifact: TravelItineraryArtifact,
    *,
    include_actions: bool = False,
) -> dict:
    """Serialize the public artifact object using camelCase fields.

    Suggested actions belong to the SSE action envelope, so they are omitted
    from the nested artifact by default.
    """

    payload = artifact.model_dump(by_alias=True, exclude_none=True)
    if not include_actions:
        payload.pop("actions", None)
    return payload


def structured_plan_dump(artifact: StructuredPlanArtifact) -> dict:
    return artifact.model_dump(by_alias=True, exclude_none=True)


def item_path_parts(path: str) -> tuple[str, str] | None:
    match = _ITEM_PATH_PATTERN.fullmatch(path)
    return (match.group("day"), match.group("item")) if match else None


def field_path_parts(path: str) -> tuple[str, str, str] | None:
    match = _FIELD_PATH_PATTERN.fullmatch(path)
    return (
        (match.group("day"), match.group("item"), match.group("field"))
        if match
        else None
    )


def items_path_day(path: str) -> str | None:
    match = _ITEMS_PATH_PATTERN.fullmatch(path)
    return match.group("day") if match else None


def plan_item_path_parts(path: str) -> tuple[str, str] | None:
    match = _PLAN_ITEM_PATH_PATTERN.fullmatch(path)
    return (match.group("period"), match.group("item")) if match else None


def plan_field_path_parts(path: str) -> tuple[str, str, str] | None:
    match = _PLAN_FIELD_PATH_PATTERN.fullmatch(path)
    return (
        (match.group("period"), match.group("item"), match.group("field"))
        if match
        else None
    )


def plan_items_path_period(path: str) -> str | None:
    match = _PLAN_ITEMS_PATH_PATTERN.fullmatch(path)
    return match.group("period") if match else None
