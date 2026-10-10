"""Deterministic boundaries for requests seeking non-public chat internals."""

from __future__ import annotations

import re
import unicodedata


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.lower())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip()


_PATTERNS = (
    r"\bignore (?:all |any |the )?(?:previous|prior)? ?(?:instructions|rules|prompts)\b",
    r"\b(?:bypass|override|disregard) (?:the )?(?:instructions|rules|guardrails)\b",
    r"\bbo qua (?:tat ca |cac )?(?:huong dan|chi dan|quy tac)\b",
    r"\b(?:system|developer|hidden) (?:prompt|instruction)s?\b",
    r"\b(?:prompt|huong dan) (?:cua (?:ban|he thong)|an)\b",
    r"\b(?:raw|full|entire|all) (?:retrieved )?(?:context|documents?|sources?)\b",
    r"\b(?:print|show|reveal|dump|list|give) .{0,48}\b(?:context|documents?|sources?)\b",
    r"\b(?:in|hien|liet ke|cho xem) .{0,48}\b(?:toan bo|nguyen van|raw) .{0,32}\b(?:context|tai lieu|nguon)\b",
    r"\bcontext hien tai\b",
    r"\bban dang dung file nao\b",
    r"\b(?:api[- ]?keys?|secrets?|passwords?|credentials?|environment variables?)\b",
    r"\b(?:filesystem|internal (?:file|path|endpoint)|private endpoint)\b",
    r"\b(?:bien moi truong|mat khau|thong tin dang nhap|duong dan noi bo|api key)\b",
)


def is_internal_extraction_request(message: str) -> bool:
    """Identify explicit extraction and bypass intent without keyword blocking."""
    value = _normalized(message)
    return any(re.search(pattern, value) for pattern in _PATTERNS)


def internal_request_refusal(locale: str = "vi") -> str:
    """Refuse without confirming any protected resource exists."""
    normalized_locale = locale.replace("_", "-").lower()
    if normalized_locale == "en" or normalized_locale.startswith("en-"):
        return "I cannot provide information that is not authorized for sharing, but I can help with questions within the available service scope."
    if normalized_locale.startswith("zh-hant"):
        return "我不能提供未經授權分享的資訊，但可以協助回答現有服務範圍內的問題。"
    if normalized_locale == "zh" or normalized_locale.startswith("zh-"):
        return "我不能提供未经授权分享的信息，但可以帮助回答现有服务范围内的问题。"
    return "Tôi không thể cung cấp thông tin chưa được phép chia sẻ, nhưng tôi có thể hỗ trợ các câu hỏi trong phạm vi dịch vụ hiện có."
