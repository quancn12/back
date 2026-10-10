"""Central response-context registry for the controlled Generative UI engine.

The registry is deliberately data-first: it describes which renderer and
components a context may use.  It does not execute arbitrary model-provided
code or allow a model to select CSS, JavaScript, or an unregistered component.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final, Literal


ResponseContext = Literal[
    "simple_answer",
    "travel_itinerary",
    "place_discovery",
    "route",
    "comparison",
    "recommendation_list",
    "shopping_comparison",
    "study_plan",
    "schedule",
    "budget",
    "data_analysis",
    "step_by_step",
    "gallery",
    "map_exploration",
    "document",
    "mixed",
]

ResponseMode = Literal[
    "text",
    "cards",
    "artifact",
    "map",
    "table",
    "comparison",
    "timeline",
    "mixed",
]


@dataclass(frozen=True)
class ResponseContextDefinition:
    id: ResponseContext
    preferred_renderer: ResponseMode
    supported_components: tuple[str, ...] = ()
    allowed_layouts: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    loading_strategy: str = "text"
    cache_strategy: str = "default"
    editable: bool = False
    patchable: bool = False


RESPONSE_REGISTRY: Final[dict[str, ResponseContextDefinition]] = {
    "simple_answer": ResponseContextDefinition(
        id="simple_answer", preferred_renderer="text"
    ),
    "travel_itinerary": ResponseContextDefinition(
        id="travel_itinerary",
        preferred_renderer="artifact",
        supported_components=(
            "TripHero",
            "DayTabs",
            "TripTimeline",
            "PlaceCard",
            "TripMap",
        ),
        allowed_layouts=("timeline_map", "mobile_stack"),
        # External place/route/image tools are intentionally not wired in this
        # MVP. The structured model receives only existing grounded context.
        tools=(),
        loading_strategy="progressive",
        cache_strategy="default",
        editable=True,
        patchable=True,
    ),
    # These contexts are intentionally registered now so routing can grow
    # without requiring component-level conditionals. They fall back to text
    # until a validated artifact contract is introduced for them.
    "place_discovery": ResponseContextDefinition("place_discovery", "text"),
    "route": ResponseContextDefinition("route", "text"),
    "comparison": ResponseContextDefinition("comparison", "text"),
    "recommendation_list": ResponseContextDefinition("recommendation_list", "text"),
    "shopping_comparison": ResponseContextDefinition("shopping_comparison", "text"),
    "study_plan": ResponseContextDefinition(
        "study_plan",
        "artifact",
        supported_components=("PlanHero", "PeriodTabs", "PlanTimeline"),
        allowed_layouts=("timeline", "mobile_stack"),
        loading_strategy="progressive",
        editable=True,
        patchable=True,
    ),
    "schedule": ResponseContextDefinition(
        "schedule",
        "artifact",
        supported_components=("PlanHero", "PeriodTabs", "PlanTimeline"),
        allowed_layouts=("timeline", "mobile_stack"),
        loading_strategy="progressive",
        editable=True,
        patchable=True,
    ),
    "budget": ResponseContextDefinition("budget", "text"),
    "data_analysis": ResponseContextDefinition("data_analysis", "text"),
    "step_by_step": ResponseContextDefinition("step_by_step", "text"),
    "gallery": ResponseContextDefinition("gallery", "text"),
    "map_exploration": ResponseContextDefinition("map_exploration", "text"),
    "document": ResponseContextDefinition("document", "text"),
    "mixed": ResponseContextDefinition("mixed", "text"),
}
# Public aliases keep the dictionary easy to discover for callers that refer
# to it as a context dictionary or response-context table.
RESPONSE_CONTEXTS = RESPONSE_REGISTRY
CONTEXT_DICTIONARY = RESPONSE_REGISTRY


def get_response_context(context_id: str) -> ResponseContextDefinition:
    """Return a registered context, defaulting unknown values to text."""

    return RESPONSE_REGISTRY.get(context_id, RESPONSE_REGISTRY["simple_answer"])


_ITINERARY_MARKERS = (
    "lịch trình",
    "lich trinh",
    "hành trình",
    "hanh trinh",
    "kế hoạch chuyến",
    "ke hoach chuyen",
    "itinerary",
    "trip plan",
    "travel plan",
    # Broad travel nouns are only itinerary signals when paired with an
    # action or duration (see _looks_like_travel_itinerary). This catches
    # machine-translated phrasing such as "nhịp đi ... cho chuyến đi" without
    # routing an ordinary statement about a trip into the artifact renderer.
    "chuyến đi",
    "chuyen di",
    "lộ trình",
    "lo trinh",
    "nhịp đi",
    "nhip di",
    "trip",
    "travel",
    "day-by-day",
    "逐日",
    "行程",
)
_EDIT_MARKERS = (
    "đổi",
    "doi",
    "thay",
    "sửa",
    "sua",
    "chỉnh",
    "chinh",
    "xóa",
    "xoa",
    "thêm",
    "them",
    "chuyển",
    "chuyen",
    "sắp xếp",
    "sap xep",
    "update",
    "change",
    "replace",
    "remove",
    "add",
    "move",
    "edit",
    "删除",
    "增加",
    "修改",
    "更换",
)
_TRAVEL_PLAN_ACTION_MARKERS = (
    "lên", "len", "lập", "lap", "tạo", "tao", "gợi ý", "goi y", "xây", "xay",
    "create", "make", "build", "plan", "prepare", "organize", "suggest", "recommend",
    "show", "give", "cho toi", "please", "muốn", "muon",
)
_PLAN_EXPLANATION_MARKERS = (
    "what is", "what's", "meaning of", "define", "definition", "explain", "explanation",
    "định nghĩa", "dinh nghia", "giải thích", "giai thich", "khái niệm", "khai niem",
    "nghĩa của", "nghia cua",
)
_DURATION_MARKER = re.compile(
    r"(?:\b\d+\s*(?:ngày|ngay|day|days|tuần|tuan|week|weeks|tháng|thang|month|months)\b|"
    r"\b(?:weekend|multi-week|month-long|nhiều tuần|nhieu tuan)\b|\b\d+\s*天)",
    re.IGNORECASE,
)
_STUDY_PLAN_MARKERS = (
    "study plan", "study schedule", "learning plan", "lesson plan",
    "kế hoạch học", "ke hoach hoc", "lịch học", "lich hoc", "ôn thi", "on thi",
    "学习计划", "学习安排",
)
_SCHEDULE_MARKERS = (
    "workout plan", "work schedule", "project schedule", "weekly schedule",
    "lịch làm việc", "lich lam viec", "lịch tập", "lich tap", "lịch trình công việc",
    "lich trinh cong viec", "日程安排", "工作计划",
)
_GENERIC_PLAN_MARKERS = (
    "plan", "planning", "kế hoạch", "ke hoach", "schedule", "安排", "计划",
)


def classify_response_context(message: str, *, has_current_artifact: bool = False) -> ResponseContextDefinition:
    """Choose a bounded presentation context from task signals.

    Definitions and ordinary factual questions remain text-first. Structured
    contexts require task-shaped wording rather than a destination keyword or
    a single copied template marker.
    """

    normalized = " ".join(message.casefold().split())
    if has_current_artifact and any(_marker_matches(normalized, marker) for marker in _EDIT_MARKERS):
        return RESPONSE_REGISTRY["travel_itinerary"]
    if _is_explanatory_plan_question(normalized):
        return RESPONSE_REGISTRY["simple_answer"]
    if _looks_like_travel_itinerary(normalized):
        return RESPONSE_REGISTRY["travel_itinerary"]
    if any(_marker_matches(normalized, marker) for marker in _STUDY_PLAN_MARKERS):
        return RESPONSE_REGISTRY["study_plan"]
    if any(_marker_matches(normalized, marker) for marker in _SCHEDULE_MARKERS):
        return RESPONSE_REGISTRY["schedule"]
    if _looks_like_generic_plan(normalized):
        return RESPONSE_REGISTRY["schedule"]
    return RESPONSE_REGISTRY["simple_answer"]


def is_itinerary_edit_request(message: str) -> bool:
    normalized = " ".join(message.casefold().split())
    return any(_marker_matches(normalized, marker) for marker in _EDIT_MARKERS)


def asks_to_build_itinerary(message: str) -> bool:
    """An itinerary noun plus an action or a duration, not the noun alone.

    "这个行程多少钱" names a trip (行程) but asks its price; "Lên lịch trình 2
    ngày, chi phí tiết kiệm" asks for a plan. The price answer (C1) uses this
    to tell the two apart.
    """
    normalized = " ".join(message.casefold().split())
    return _looks_like_travel_itinerary(normalized) and bool(
        _DURATION_MARKER.search(normalized)
        or any(_marker_matches(normalized, marker) for marker in _TRAVEL_PLAN_ACTION_MARKERS)
    )


def is_ambiguous_travel_request(
    message: str,
    *,
    has_destination: bool = False,
    has_duration: bool = False,
) -> bool:
    """Return whether a travel-planning request lacks its two core anchors.

    A model may fill optional details, but it must not choose a destination or
    trip length for a request that supplies neither. Callers can pass bounded
    conversation memory so a follow-up such as "lập lịch trình" remains valid
    after the user has already stated those anchors.
    """

    return (
        _looks_like_travel_itinerary(" ".join(message.casefold().split()))
        and not has_destination
        and not has_duration
    )


def _looks_like_generic_plan(text: str) -> bool:
    if not any(_marker_matches(text, marker) for marker in _GENERIC_PLAN_MARKERS):
        return False
    return bool(
        _DURATION_MARKER.search(text)
        or any(_marker_matches(text, marker) for marker in _TRAVEL_PLAN_ACTION_MARKERS)
    )


def _marker_matches(text: str, marker: str) -> bool:
    """Match a phrase as a token, avoiding ``add`` inside ``address``."""

    if any("\u3400" <= char <= "\u9fff" for char in marker):
        return marker in text
    return re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", text) is not None


def _is_explanatory_plan_question(text: str) -> bool:
    return any(_marker_matches(text, marker) for marker in _PLAN_EXPLANATION_MARKERS)


def _looks_like_travel_itinerary(text: str) -> bool:
    if not any(_marker_matches(text, marker) for marker in _ITINERARY_MARKERS):
        return False
    # Strong Vietnamese itinerary nouns are already task-shaped. English nouns
    # such as "itinerary" need an action or duration signal to avoid routing
    # definition/format questions into an artifact.
    if any(_marker_matches(text, marker) for marker in ("lịch trình", "lich trinh", "hành trình", "hanh trinh", "逐日", "行程")):
        return True
    return bool(
        _DURATION_MARKER.search(text)
        or any(_marker_matches(text, marker) for marker in _TRAVEL_PLAN_ACTION_MARKERS)
    )
