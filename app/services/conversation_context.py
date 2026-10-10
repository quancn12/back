"""Bounded, conversation-scoped context extraction for unified chat.

This module deliberately does not create a profile or train a model.  It reads
recent turns from one conversation and one mode, extracts a small set of user
planning constraints, and lets the latest value for each field win.  Assistant
turns contribute only field-name markers used to avoid redundant suggestions;
their text and values never enter prompt memory.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


MAX_MEMORY_TURNS = 64
MAX_VALUE_LENGTH = 80
MAX_PREFERENCE_COUNT = 6

_FIELD_ORDER = ("destination", "duration", "budget", "goal", "pace", "interest", "constraint")

_CORRECTION_MARKERS = (
    "không phải", "khong phai", "sửa lại", "sua lai", "đính chính", "dinh chinh",
    "thực ra", "thuc ra", "nói nhầm", "noi nham", "đổi thành", "doi thanh",
    "actually", "correct that", "correction", "change it to", "not ",
    "不是", "改为", "更正", "其实",
)

_STOP_TAIL = r"(?:\s+(?:trong|với|voi|và|va|ngân sách|ngan sach|budget|for|with)\b|[,.;!?]|$)"
_DESTINATION_PATTERNS = (
    re.compile(r"\b(?:đổi thành|doi thanh|change it to|instead use|改为|更正为)\s+(.{2,80}?)" + _STOP_TAIL, re.IGNORECASE),
    re.compile(r"\b(?:đi|di|đến|den|tới|toi|visit|travel to|go(?:\s+to)?)\s+(.{2,80}?)" + _STOP_TAIL, re.IGNORECASE),
    re.compile(r"\b(?:ở|o|tại|tai|in)\s+([A-Za-zÀ-ỹĐđ][A-Za-zÀ-ỹĐđ\s-]{1,70}?)" + _STOP_TAIL, re.IGNORECASE),
    re.compile(r"(?:去|到|在|改为)([\u3400-\u9fff]{2,20})"),
)
_DURATION_RE = re.compile(
    r"\b(?:([0-9]{1,2})\s*(?:ngày|ngay|đêm|dem|days?|nights?|tuần|tuan|weeks?)|"
    r"(?:a|one|một|mot|một|mot)\s+(?:week|tuần|tuan))\b|"
    r"[0-9]{1,2}\s*(?:天|日|晚|夜)",
    re.IGNORECASE,
)
_BUDGET_PATTERNS = (
    re.compile(r"(?:ngân sách|ngan sach|budget|spend|chi khoảng|chi khoang)\s*(?:là|la|of|:)?\s*([$€£]?\s*[0-9][0-9.,]*\s*(?:triệu|trieu|million|nghìn|nghin|k|m|tr)?(?:\s*(?:vnd|đ|dollars?|usd))?)", re.IGNORECASE),
    re.compile(r"([$€£]?\s*[0-9][0-9.,]*\s*(?:triệu|trieu|million|nghìn|nghin|k|m|tr)\b(?:\s*(?:vnd|đ|dollars?|usd))?)", re.IGNORECASE),
    re.compile(r"(?:预算|預算)(?:约|約|为|為|是|[:：])?\s*([0-9][0-9.,]*\s*(?:元|人民币|人民幣|¥))"),
)
_TRAVELERS_RE = re.compile(
    r"(?:\b(?:nhóm|nhom|cho)\s*(?:khoảng|khoang)?\s*[0-9]{1,2}\s*(?:người|nguoi|khách|khach)\b|"
    r"\b(?:party|group)\s+of\s+[0-9]{1,2}\b|"
    r"\b[0-9]{1,2}\s*(?:people|travell?ers?|guests?)\b|"
    r"[0-9]{1,2}\s*(?:人|位))",
    re.IGNORECASE,
)
_GOAL_PATTERNS = (
    re.compile(r"\b(?:muốn|muon|cần|can|tìm|tim|lên kế hoạch|len ke hoach|want|need|looking for|plan)\s+(.{2,100}?)" + _STOP_TAIL, re.IGNORECASE),
)

_PREFERENCE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pace", ("chậm", "cham", "slow", "relaxed", "thư thả", "thu tha", "nhẹ nhàng", "nhe nhang", "packed", "nhiều điểm", "nhieu diem")),
    ("interest", ("ẩm thực", "am thuc", "food", "ăn uống", "an uong", "văn hóa", "van hoa", "culture", "thiên nhiên", "thien nhien", "nature", "biển", "bien", "beach", "mạo hiểm", "mao hiem", "adventure")),
    ("constraint", ("gia đình", "gia dinh", "family", "trẻ em", "tre em", "kids", "người lớn tuổi", "nguoi lon tuoi", "rain", "mưa", "mua", "trời mưa", "troi mua", "indoor", "trong nhà", "trong nha")),
)
_NEGATIVE_RE = re.compile(r"\b(?:không thích|khong thich|không muốn|khong muon|avoid|don't want|do not want)\s+(.{2,80}?)" + _STOP_TAIL, re.IGNORECASE)
_POSITIVE_RE = re.compile(r"\b(?:thích|thich|ưu tiên|uu tien|prefer|like|want)\s+(.{2,80}?)" + _STOP_TAIL, re.IGNORECASE)


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFD", value.casefold())
    return "".join(char for char in value if unicodedata.category(char) != "Mn").replace("đ", "d")


def _clean(value: str, *, max_length: int = MAX_VALUE_LENGTH) -> str:
    value = re.sub(r"\s+", " ", value.strip(" \t\r\n,.;:!?-"))
    return value[:max_length].rstrip(" ,.;:!?-")


def _contains(value: str, needle: str) -> bool:
    folded = _fold(value)
    candidate = _fold(needle)
    return bool(re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", folded)) or candidate in folded if len(candidate) > 3 else False


def _is_correction(message: str) -> bool:
    folded = _fold(message)
    return any(_fold(marker) in folded for marker in _CORRECTION_MARKERS)


def _extract_destination(message: str) -> str | None:
    for pattern in _DESTINATION_PATTERNS:
        match = pattern.search(message)
        if match:
            value = _clean(match.group(1))
            if value and not _is_generic(value):
                return value
    return None


def _is_generic(value: str) -> bool:
    folded = _fold(value)
    if folded in {
        "there", "day", "week", "somewhere", "đau", "dau", "đó", "do",
        # These are travel-planning nouns, not place names. Treating them as a
        # destination defeats the clarification gate for malformed requests.
        "thực tế", "thuc te", "nhịp đi", "nhip di", "chuyến đi", "chuyen di",
        "lộ trình", "lo trinh", "hành trình", "hanh trinh",
    }:
        return True
    return folded.startswith(("thuc te ", "nhip di ", "chuyen di ", "lo trinh ", "hanh trinh "))


def _extract_duration(message: str) -> str | None:
    match = _DURATION_RE.search(message)
    if not match:
        return None
    value = _clean(match.group(0))
    if re.search(r"week|tuần|tuan", _fold(value)):
        return value
    return value


def _extract_budget(message: str) -> str | None:
    for pattern in _BUDGET_PATTERNS:
        match = pattern.search(message)
        if match:
            return _clean(match.group(1))
    return None


def _assistant_answered_fields(message: str) -> set[str]:
    """Return only planning field names explicitly answered in assistant text."""

    answered: set[str] = set()
    if _extract_duration(message):
        answered.add("duration")
    if _extract_budget(message):
        answered.add("budget")
    if _TRAVELERS_RE.search(message):
        answered.add("travelers")
    return answered


def _extract_goal(message: str) -> str | None:
    for pattern in _GOAL_PATTERNS:
        match = pattern.search(message)
        if match:
            value = _clean(match.group(1))
            if value and not _is_generic(value):
                return value
    return None


def _extract_preference(message: str) -> tuple[str, str] | None:
    preferences = _extract_preferences(message)
    return preferences[-1] if preferences else None


def _extract_preferences(message: str) -> list[tuple[str, str]]:
    preferences: list[tuple[str, str]] = []
    folded = _fold(message)
    negative = _NEGATIVE_RE.search(message)
    if negative:
        preferences.append(("constraint", _clean("avoid " + negative.group(1))))
    positive = _POSITIVE_RE.search(message)
    if positive:
        value = _clean(positive.group(1))
        # "I want to go Hue" is a destination/goal, not an interest. Keep
        # this parser from persisting the old destination as a preference
        # after a later correction.
        if value and not re.match(r"(?:to\s+)?(?:go|visit|travel)\b", _fold(value)):
            preferences.append(("interest", value))
    occurrences: list[tuple[int, str, str]] = []
    for key, terms in _PREFERENCE_PATTERNS:
        for term in terms:
            position = folded.find(_fold(term))
            if position >= 0:
                occurrences.append((position, key, term))
    # Preserve the latest phrase within the turn for each category, matching
    # the same later-wins rule used across turns.
    latest: dict[str, tuple[int, str]] = {}
    for position, key, term in sorted(occurrences):
        latest[key] = (position, term)
    preferences.extend((key, value) for key, (_, value) in latest.items())
    return preferences


@dataclass
class ConversationMemory:
    """Small request-time memory, intentionally scoped to one conversation/mode."""

    destination: str | None = None
    duration: str | None = None
    budget: str | None = None
    goal: str | None = None
    preferences: dict[str, str] = field(default_factory=dict)
    corrections: list[str] = field(default_factory=list)
    answered_fields: set[str] = field(default_factory=set)

    def apply(self, message: str) -> None:
        message = _clean(message, max_length=4000)
        corrected = _is_correction(message)
        previous_destination = self.destination
        next_destination = _extract_destination(message)
        next_goal = _extract_goal(message)
        if (
            next_destination
            and previous_destination
            and _fold(next_destination) != _fold(previous_destination)
        ) or (
            next_goal and self.goal and _fold(next_goal) != _fold(self.goal)
        ):
            # Assistant answers belong to one planning epoch. A new
            # destination/goal must be free to ask for its own missing fields.
            self.answered_fields.clear()
        updates: list[tuple[str, str | None]] = [
            ("destination", next_destination),
            ("duration", _extract_duration(message)),
            ("budget", _extract_budget(message)),
            ("goal", next_goal),
        ]
        preferences = _extract_preferences(message)
        for preference in preferences:
            self.preferences[preference[0]] = preference[1]
            updates.append((preference[0], preference[1]))
        for field_name, value in updates:
            if value:
                if field_name == "destination":
                    self.destination = value
                    if corrected and previous_destination and self.goal:
                        self.goal = self.goal.replace(previous_destination, value)
                elif field_name == "duration":
                    self.duration = value
                elif field_name == "budget":
                    self.budget = value
                elif field_name == "goal":
                    self.goal = value
                elif field_name not in {"destination", "duration", "budget", "goal"}:
                    self.preferences[field_name] = value
                if corrected:
                    self.corrections.append(f"{field_name} = {value}")
        if len(self.corrections) > MAX_PREFERENCE_COUNT:
            self.corrections = self.corrections[-MAX_PREFERENCE_COUNT:]

    def copy(self) -> "ConversationMemory":
        return ConversationMemory(
            destination=self.destination,
            duration=self.duration,
            budget=self.budget,
            goal=self.goal,
            preferences=dict(self.preferences),
            corrections=list(self.corrections),
            answered_fields=set(self.answered_fields),
        )

    def note_assistant_answers(self, message: str) -> None:
        self.answered_fields.update(_assistant_answered_fields(message))

    def with_message(self, message: str) -> "ConversationMemory":
        result = self.copy()
        result.apply(message)
        return result

    def fields(self) -> dict[str, str]:
        values = {
            "destination": self.destination,
            "duration": self.duration,
            "budget": self.budget,
            "goal": self.goal,
            **self.preferences,
        }
        return {key: value for key, value in values.items() if value}

    def prompt_text(self) -> str:
        fields = self.fields()
        if not fields and not self.corrections:
            return ""
        labels = {
            "destination": "destination",
            "duration": "trip duration",
            "budget": "budget",
            "goal": "current goal",
            "pace": "pace",
            "interest": "interest",
            "constraint": "constraint",
        }
        lines = [f"- {labels.get(key, key)}: {value}" for key, value in fields.items()]
        if self.corrections:
            lines.append("- latest user corrections: " + "; ".join(self.corrections[-MAX_PREFERENCE_COUNT:]))
        return "\n".join(lines)

    def query_hint(self) -> str:
        values = self.fields()
        parts = [values.get(key) for key in _FIELD_ORDER if values.get(key)]
        return " ".join(parts)


def build_conversation_memory(
    messages: list[dict],
    *,
    mode: str,
    destination_slug: str | None = None,
    current_message: str | None = None,
) -> ConversationMemory:
    """Extract bounded same-mode memory; no other conversation can enter here."""

    memory = ConversationMemory()
    if destination_slug:
        memory.destination = _clean(destination_slug.replace("-", " "))
    for item in messages[-MAX_MEMORY_TURNS:]:
        if item.get("context_type", mode) != mode:
            continue
        role = item.get("role")
        content = str(item.get("content") or "")
        if role == "user":
            memory.apply(content)
        elif role == "assistant":
            memory.note_assistant_answers(content)
    if current_message:
        memory.apply(current_message)
    return memory


def effective_destination_scope(
    destination_slug: str | None,
    memory_destination: str | None,
) -> str | None:
    """Keep an editorial scope only while it agrees with explicit travel memory.

    A user can revise a destination in chat after the UI selected a scoped
    destination.  Passing the stale slug to retrieval would filter out the
    corrected destination before semantic or keyword ranking runs.  Slugs are
    only retained for an exact normalized match; a mismatch falls back to the
    country-wide query whose text still carries the corrected destination.
    """

    if not destination_slug or not memory_destination:
        return destination_slug
    scoped = _fold(destination_slug.replace("-", " ")).strip()
    if scoped.startswith("demo "):
        scoped = scoped[5:]
    current = _fold(memory_destination).strip()
    return destination_slug if scoped == current else None


def context_suggestions(memory: ConversationMemory, *, locale: str, mode: str) -> list[dict[str, str]]:
    """Return up to two safe, contextual follow-ups for the UI composer."""

    if mode != "TRAVEL":
        return []
    locale = locale if locale in {"vi", "en", "zh-Hans"} else "vi"
    fields = memory.fields()
    destination = fields.get("destination")
    if not destination and not fields.get("goal"):
        return []
    suggestions: list[dict[str, str]] = []
    if destination and not fields.get("duration") and "duration" not in memory.answered_fields:
        labels = {
            "vi": ("Thêm thời lượng chuyến đi", f"Mình muốn đi {destination} trong bao nhiêu ngày thì hợp lý?"),
            "en": ("Add trip length", f"How many days should I plan for {destination}?"),
            "zh-Hans": ("补充行程天数", f"去{destination}安排几天比较合适？"),
        }
        label, prompt = labels[locale]
        suggestions.append({"id": "context-duration", "label": label, "prompt": prompt})
    if (
        destination
        and fields.get("duration")
        and not fields.get("budget")
        and "budget" not in memory.answered_fields
    ):
        labels = {
            "vi": ("Thêm ngân sách", f"Gợi ý cho mình lựa chọn ở {destination} trong {fields['duration']} theo ngân sách phù hợp."),
            "en": ("Add a budget", f"Suggest options in {destination} for {fields['duration']} at a suitable budget."),
            "zh-Hans": ("补充预算", f"请按合适的预算，为我安排{destination}{fields['duration']}的选择。"),
        }
        label, prompt = labels[locale]
        suggestions.append({"id": "context-budget", "label": label, "prompt": prompt})
    if destination and fields.get("duration") and fields.get("budget"):
        preference = fields.get("interest") or fields.get("pace") or fields.get("constraint")
        suffix = f" ưu tiên {preference}" if preference and locale == "vi" else f", prioritising {preference}" if preference and locale == "en" else f"，优先考虑{preference}" if preference else ""
        if locale == "vi":
            label, prompt = "Tóm tắt theo lựa chọn", f"Tóm tắt kế hoạch ở {destination} trong {fields['duration']} với ngân sách {fields['budget']}{suffix}."
        elif locale == "en":
            label, prompt = "Summarize my plan", f"Summarize a plan for {destination} for {fields['duration']} with a budget of {fields['budget']}{suffix}."
        else:
            label, prompt = "总结我的计划", f"请总结{destination}{fields['duration']}、预算为{fields['budget']}的计划{suffix}。"
        suggestions.append({"id": "context-summary", "label": label, "prompt": prompt})
    if fields.get("goal") and not destination:
        labels = {
            "vi": ("Chọn điểm đến", "Gợi ý điểm đến phù hợp với mục tiêu này."),
            "en": ("Choose a destination", "Suggest destinations that fit this goal."),
            "zh-Hans": ("选择目的地", "请推荐符合这个目标的目的地。"),
        }
        label, prompt = labels[locale]
        suggestions.append({"id": "context-destination", "label": label, "prompt": prompt})
    return suggestions[:2]


def filter_answered_suggestions(
    suggestions: list[dict[str, str]], assistant_reply: str
) -> list[dict[str, str]]:
    """Drop prompts for planning fields answered in the just-finished reply.

    Suggestions are initially planned before generation so they can use the
    user's bounded memory. This final deterministic filter fixes that timing
    gap without feeding assistant prose back into memory or changing the SSE
    payload shape.
    """

    answered = _assistant_answered_fields(assistant_reply)
    suggestion_fields = {
        "context-duration": "duration",
        "context-budget": "budget",
    }
    return [
        suggestion
        for suggestion in suggestions
        if suggestion_fields.get(suggestion.get("id", "")) not in answered
    ]
