"""Small, deterministic response-preference hints for chat prompts.

The parser only extracts explicit preferences or a high-confidence language
signal from the current user turn. It does not choose a personality, a fixed
answer length, or a presentation mode for the model.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


SUPPORTED_LOCALES = {"vi", "en", "zh-Hans"}
LANGUAGE_NAMES = {
    "vi": "Vietnamese",
    "en": "English",
    "zh-Hans": "Simplified Chinese",
}


@dataclass(frozen=True)
class ResponsePreferences:
    """Preferences that are safe to express as fixed system instructions."""

    locale: str
    language_requested: bool = False
    detail: str | None = None
    tone: str | None = None
    format_hint: str | None = None


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFD", value.casefold())
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    value = value.replace("đ", "d")
    return re.sub(r"\s+", " ", value).strip()


def _has_any(value: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in value for phrase in phrases)


def _explicit_language(message: str) -> str | None:
    normalized = _normalize(message)

    # Keep these patterns deliberately narrow: mentioning an English or
    # Chinese place/name should not silently switch the answer language.
    if re.search(
        r"(?:\b(?:in|using|by)\s+english\b|\benglish\s+please\b|"
        r"\b(?:tra loi|viet|noi|giai thich|answer|reply|respond)\b[^.!?\n]{0,80}"
        r"\b(?:tieng anh|english)\b)",
        normalized,
    ):
        return "en"
    if re.search(
        r"(?:\b(?:in|using|by)\s+vietnamese\b|\b(?:tieng viet|vietnamese)\s+please\b|"
        r"\b(?:tra loi|viet|noi|giai thich|answer|reply|respond)\b[^.!?\n]{0,80}"
        r"\b(?:tieng viet|vietnamese)\b)",
        normalized,
    ):
        return "vi"
    if re.search(
        r"(?:\b(?:in|using|by)\s+(?:simplified\s+)?chinese\b|"
        r"\b(?:chinese|tieng trung|中文|简体中文)\s+please\b|"
        r"(?:tra loi|viet|noi|giai thich|answer|reply|respond)\b[^.!?\n]{0,80}"
        r"\b(?:tieng trung|chinese|中文|简体中文)\b)",
        normalized,
    ):
        return "zh-Hans"
    # Common direct forms in Chinese do not have whitespace after the verb.
    if re.search(r"(?:用|使用).{0,8}(?:中文|简体中文)(?:回答|回复|解释)?", message):
        return "zh-Hans"
    return None


def _inferred_language(message: str, fallback: str) -> str:
    return _language_signal(message) or (fallback if fallback in SUPPORTED_LOCALES else "vi")


def _language_signal(message: str) -> str | None:
    explicit = _explicit_language(message)
    if explicit:
        return explicit
    if re.search(r"[\u3400-\u9fff]", message):
        return "zh-Hans"

    normalized = _normalize(message)
    # This list intentionally favors Vietnamese markers that are unlikely to
    # appear in an English sentence. Unaccented Vietnamese remains supported.
    vietnamese_markers = (
        "xin chao", "toi", "minh", "ban", "muon", "giup", "cho toi", "o dau",
        "bao nhieu", "nhu the nao", "khach san", "diem den", "lich trinh",
        "giai thich", "ngay", "gio", "co the", "khong", "cam on",
    )
    if _has_any(normalized, vietnamese_markers):
        return "vi"

    english_markers = (
        "what", "where", "when", "which", "how", "please", "could", "would",
        "can i", "tell me", "explain", "recommend", "schedule", "hotel",
        "destination", "cancel", "refund", "rebook", "reschedule",
    )
    if sum(marker in normalized for marker in english_markers) >= 1:
        return "en"
    return None


def language_signal(message: str) -> str | None:
    """Return a high-confidence language signal from one user turn, if any."""

    return _language_signal(message)


def response_preferences(message: str, fallback_locale: str = "vi") -> ResponsePreferences:
    """Extract current-turn language, detail, tone, and format preferences."""

    fallback = fallback_locale if fallback_locale in SUPPORTED_LOCALES else "vi"
    normalized = _normalize(message)
    explicit_language = _explicit_language(message)
    locale = explicit_language or _inferred_language(message, fallback)

    detail: str | None = None
    if _has_any(
        normalized,
        (
            "ngan gon", "ngan thoi", "tom tat", "mot cau", "one sentence",
            "brief", "concise", "short answer", "keep it short",
        ),
    ):
        detail = "concise"
    elif _has_any(
        normalized,
        (
            "chi tiet", "giai thich ky", "giai thich day du", "tung buoc",
            "phan tich", "in detail", "detailed", "thorough", "step by step",
        ),
    ):
        detail = "detailed"

    tone: str | None = None
    if _has_any(normalized, ("than mat", "tu nhien", "casual", "friendly", "nhu noi chuyen")):
        tone = "casual"
    elif _has_any(normalized, ("trang trong", "chuyen nghiep", "formal", "professional")):
        tone = "formal"

    format_hint: str | None = None
    if _has_any(normalized, ("khong dung bullet", "khong dung gach dau dong", "no bullets", "no bullet")):
        format_hint = "no_bullets"
    elif _has_any(normalized, ("bang", "table", "as a table")) and not _has_any(
        normalized, ("bang bao nhieu", "bang gia", "bang tien")
    ):
        format_hint = "table"
    elif _has_any(normalized, ("gach dau dong", "bullet points", "as bullets")):
        format_hint = "bullets"

    return ResponsePreferences(
        locale=locale,
        language_requested=explicit_language is not None,
        detail=detail,
        tone=tone,
        format_hint=format_hint,
    )


def prompt_guidance(
    message: str,
    fallback_locale: str = "vi",
    *,
    task_needs_detail: bool = False,
) -> tuple[ResponsePreferences, str]:
    """Return preferences and concise prompt guidance for a current turn."""

    preferences = response_preferences(message, fallback_locale)
    language = LANGUAGE_NAMES[preferences.locale]
    parts = [
        f"Write the answer in {language}; an explicit language request in the current user turn overrides the conversation locale.",
        "Address the latest request first. Use prior turns only to resolve references or follow-up changes.",
        "Scale detail to the task: keep simple questions compact and develop explanations or plans enough to be useful; do not pad.",
        "Do not assume a fixed duration, section count, greeting, or bullet count unless the user asked for it.",
        "Do not add a greeting unless the user is greeting you or it is needed for a natural continuation.",
        "Honor an explicit output format request when it is compatible with grounding and safety.",
        "Use grounded facts only when directly relevant to the current question; do not advertise unrelated facts or append a generic offer. If evidence is insufficient, state the specific gap and ask one relevant clarification or propose one bounded truthful next step.",
        "Use general knowledge only for abstract definitions or explanations; travel and hotel plans, recommendations, and product claims must stay within directly supported grounded facts.",
    ]
    if preferences.detail == "concise":
        parts.append("The user asked for a concise response; keep only the essential answer and requested facts.")
    elif preferences.detail == "detailed" or task_needs_detail:
        parts.append("The request benefits from enough detail to understand or act; organize it only where that improves clarity.")
    if preferences.tone == "casual":
        parts.append("Use a natural conversational tone that follows the user's casual register.")
    elif preferences.tone == "formal":
        parts.append("Use a professional, respectful tone without scripted warmth or filler.")
    if preferences.format_hint == "no_bullets":
        parts.append("The user asked for prose without bullet points.")
    elif preferences.format_hint == "table":
        parts.append("The user asked for a table; use one only when the facts genuinely compare cleanly.")
    elif preferences.format_hint == "bullets":
        parts.append("The user asked for bullet points; keep them focused on the requested information.")
    return preferences, " ".join(parts)
