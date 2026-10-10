"""Ollama client used for local and Google Compute Engine deployments.

Both chat generation and embeddings use the same Ollama REST service. The
module keeps the historical ``ollama_client`` alias so the legacy hotel routes
can continue importing it while the travel routes use ``llm_client``.
"""
from contextvars import ContextVar
from typing import AsyncGenerator
import json
import math
import re

import httpx

from app.core.config import settings


OUTPUT_LIMIT_NOTICE = (
    "\n\n---\nPhản hồi đã chạm giới hạn độ dài và chưa hoàn tất. "
    "Bạn có thể yêu cầu tiếp tục phần còn lại."
)

_LONG_RESPONSE_MARKERS = (
    "lịch trình", "lich trinh", "kế hoạch", "ke hoach", "từng ngày",
    "tung ngay", "chi tiết", "chi tiet", "đầy đủ", "day du", "so sánh",
    "so sanh", "itinerary", "day-by-day", "detailed", "compare", "行程",
    "详细", "逐日", "比较",
)
_MULTI_DAY_RESPONSE_PATTERN = re.compile(
    r"(?:\b(?:[2-9]|[1-9]\d)\s*(?:ngày|ngay|days?)\b|\b\d+\s*天)",
    re.IGNORECASE,
)


_ACTIVE_CHAT_USAGE: ContextVar[dict | None] = ContextVar(
    "vietsage_active_chat_usage", default=None
)
_LAST_CHAT_USAGE: ContextVar[dict | None] = ContextVar(
    "vietsage_last_chat_usage", default=None
)
_ACTIVE_CHAT_OPTIONS: ContextVar[dict | None] = ContextVar(
    "vietsage_active_chat_options", default=None
)
_OLLAMA_USAGE_FIELDS = (
    "total_duration",
    "load_duration",
    "prompt_eval_count",
    "prompt_eval_cached_count",
    "prompt_eval_duration",
    "eval_count",
    "eval_duration",
)


def _record_ollama_usage(payload: dict) -> None:
    usage = _ACTIVE_CHAT_USAGE.get()
    if usage is None:
        return
    usage.clear()
    for field in _OLLAMA_USAGE_FIELDS:
        value = payload.get(field)
        if isinstance(value, (int, float)) and value >= 0:
            usage[field] = value
    done_reason = payload.get("done_reason")
    if isinstance(done_reason, str) and done_reason:
        usage["done_reason"] = done_reason


def current_chat_usage() -> dict:
    """Return numeric provider counters for the current async request only."""
    return dict(_LAST_CHAT_USAGE.get() or {})


def estimate_tokens(text: str) -> int:
    """Estimate provider tokens without treating Vietnamese UTF-8 bytes as tokens.

    Ollama's tokenizer is model-specific, so this is only a planning heuristic.
    Three Unicode characters per token is intentionally conservative for the
    Vietnamese prompts used here while avoiding the previous 3x UTF-8
    overestimate that disabled extended answers almost all the time.
    """
    return math.ceil(len(text) / 3) if text else 0


def estimate_message_tokens(messages: list[dict]) -> int:
    # Reserve a small fixed envelope for message roles/separators. Ollama's
    # model tokenizer is not available at planning time, so this remains an
    # explicitly labelled upper-bound heuristic rather than a token count.
    return sum(estimate_tokens(str(message.get("content") or "")) + 4 for message in messages)


def output_budget_for(messages: list[dict], *, long_request: bool) -> int | None:
    """Choose a larger output budget only when a long request has input headroom.

    Ollama's tokenizer is model-specific and not available here, so this uses a
    deliberately bounded character heuristic and leaves the configured default
    untouched whenever there is not enough estimated context room.
    """
    if not long_request:
        return None
    default = settings.ollama_num_predict
    # Ollama accepts negative values (for example -1) for an unbounded output
    # mode. Preserve that configured behavior instead of replacing it with a
    # positive cap.
    if default < 0:
        return None
    desired = max(default, settings.ollama_long_num_predict)
    estimated_input = estimate_message_tokens(messages)
    available = settings.ollama_num_ctx - estimated_input - settings.ollama_context_reserve_tokens
    if available < default:
        return None
    return min(desired, available)


def context_budget_for(messages: list[dict], *, long_request: bool) -> int | None:
    """Use a smaller KV cache for short prompts when the response still fits."""
    if long_request:
        return None
    configured = settings.ollama_num_ctx
    short_context = min(configured, settings.ollama_short_num_ctx)
    estimated_input = estimate_message_tokens(messages)
    required = estimated_input + settings.ollama_num_predict + settings.ollama_context_reserve_tokens
    if required <= short_context:
        return short_context
    return None


def requests_extended_output(message: str) -> bool:
    """Recognize explicit requests whose useful answer normally needs more space."""
    normalized = message.casefold()
    return (
        any(marker in normalized for marker in _LONG_RESPONSE_MARKERS)
        or bool(_MULTI_DAY_RESPONSE_PATTERN.search(normalized))
    )


def _partial_tag_suffix(text: str, tags: tuple[str, ...]) -> int:
    """Length of a suffix that may be the beginning of a split XML tag."""
    return max(
        (
            size
            for tag in tags
            for size in range(1, min(len(tag), len(text) + 1))
            if text.endswith(tag[:size])
        ),
        default=0,
    )


async def strip_thinking_stream(
    chunks: AsyncGenerator[str, None],
) -> AsyncGenerator[str, None]:
    """Remove ``<think>`` blocks without buffering the visible answer."""
    open_tag = "<think>"
    close_tag = "</think>"
    tags = (open_tag, close_tag)
    buffer = ""
    inside_thinking = False

    try:
        async for chunk in chunks:
            if not chunk:
                continue
            buffer += chunk

            while buffer:
                if inside_thinking:
                    end = buffer.find(close_tag)
                    if end < 0:
                        keep = _partial_tag_suffix(buffer, (close_tag,))
                        buffer = buffer[-keep:] if keep else ""
                        break
                    buffer = buffer[end + len(close_tag):]
                    inside_thinking = False
                    continue

                open_at = buffer.find(open_tag)
                close_at = buffer.find(close_tag)
                positions = [pos for pos in (open_at, close_at) if pos >= 0]
                if positions:
                    marker_at = min(positions)
                    visible = buffer[:marker_at]
                    if visible:
                        yield visible
                    if marker_at == open_at:
                        buffer = buffer[marker_at + len(open_tag):]
                        inside_thinking = True
                    else:
                        buffer = buffer[marker_at + len(close_tag):]
                    continue

                keep = _partial_tag_suffix(buffer, tags)
                visible = buffer[:-keep] if keep else buffer
                buffer = buffer[-keep:] if keep else ""
                if visible:
                    yield visible
                break

        if buffer and not inside_thinking:
            cleaned = buffer.replace(open_tag, "").replace(close_tag, "")
            if cleaned:
                yield cleaned
    finally:
        close = getattr(chunks, "aclose", None)
        if close is not None:
            await close()


class OllamaProvider:
    """Call Ollama's REST API for chat and embeddings."""

    def __init__(self) -> None:
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.chat_model = settings.ollama_chat_model
        self.embed_model = settings.ollama_embed_model
        self.keep_alive = settings.ollama_keep_alive
        self.embed_keep_alive = settings.ollama_embed_keep_alive
        self._client: httpx.AsyncClient | None = None

    @staticmethod
    def _chat_options() -> dict[str, int | float]:
        requested = _ACTIVE_CHAT_OPTIONS.get() or {}
        return {
            "num_ctx": requested.get("num_ctx", settings.ollama_num_ctx),
            "temperature": settings.ollama_temperature,
            "num_predict": requested.get("num_predict", settings.ollama_num_predict),
        }

    async def startup(self) -> None:
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(
                max_keepalive_connections=min(10, settings.api_max_connections),
                max_connections=settings.api_max_connections,
            )
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    settings.api_timeout_seconds,
                    connect=settings.api_connect_timeout_seconds,
                ),
                limits=limits,
                transport=httpx.AsyncHTTPTransport(retries=1, limits=limits),
            )

    async def shutdown(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def _get_client(self) -> httpx.AsyncClient:
        await self.startup()
        assert self._client is not None
        return self._client

    async def chat(self, messages: list[dict]) -> str:
        client = await self._get_client()
        options = self._chat_options()
        response = await client.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.chat_model,
                "messages": messages,
                "stream": False,
                "think": settings.ollama_think,
                "keep_alive": self.keep_alive,
                "options": options,
            },
        )
        response.raise_for_status()
        result = response.json()
        _record_ollama_usage(result)
        answer = result["message"]["content"]
        if result.get("done_reason") == "length" and answer.strip():
            answer += OUTPUT_LIMIT_NOTICE
        return answer

    async def chat_json(
        self,
        messages: list[dict],
        *,
        schema: dict | None = None,
        num_predict: int | None = None,
        num_ctx: int | None = None,
    ) -> dict:
        """Request one JSON object for application-level validation."""
        client = await self._get_client()
        options = self._chat_options()
        if num_predict is not None:
            options["num_predict"] = num_predict
        if num_ctx is not None:
            options["num_ctx"] = num_ctx
        response = await client.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.chat_model,
                "messages": messages,
                "stream": False,
                "think": settings.ollama_think,
                "keep_alive": self.keep_alive,
                "format": schema or "json",
                "options": options,
            },
        )
        response.raise_for_status()
        result = response.json()
        _record_ollama_usage(result)
        content = result.get("message", {}).get("content", "")
        if not content:
            raise RuntimeError("Ollama returned an empty structured response.")
        try:
            cleaned_content = re.sub(r"<think>[\s\S]*?</think>\s*", "", content)
            cleaned_content = re.sub(r"<think>[\s\S]*", "", cleaned_content)
            parsed = json.loads(cleaned_content.strip())
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Ollama returned invalid structured JSON.") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Ollama structured response must be a JSON object.")
        return parsed

    async def chat_stream(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        client = await self._get_client()
        options = self._chat_options()
        async with client.stream(
            "POST",
            f"{self.base_url}/api/chat",
            json={
                "model": self.chat_model,
                "messages": messages,
                "stream": True,
                "think": settings.ollama_think,
                "keep_alive": self.keep_alive,
                "options": options,
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise RuntimeError("Ollama streaming request failed")
                content = chunk.get("message", {}).get("content", "")
                if content:
                    yield content
                if chunk.get("done"):
                    _record_ollama_usage(chunk)
                    if chunk.get("done_reason") == "length":
                        yield OUTPUT_LIMIT_NOTICE
                    return
            raise RuntimeError("Ollama stream ended before completion")

    async def embed(self, text: str) -> list[float]:
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        clean_texts = [text.strip() for text in texts if text and text.strip()]
        if not clean_texts:
            return []

        client = await self._get_client()
        response = await client.post(
            f"{self.base_url}/api/embed",
            json={
                "model": self.embed_model,
                "input": clean_texts,
                "keep_alive": self.embed_keep_alive,
            },
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()
        embeddings = data.get("embeddings", [])
        if len(embeddings) != len(clean_texts):
            raise RuntimeError(
                f"Ollama returned {len(embeddings)} embeddings for "
                f"{len(clean_texts)} texts."
            )
        return embeddings

    async def health_check(self) -> dict:
        client = await self._get_client()
        response = await client.get(f"{self.base_url}/api/tags", timeout=5.0)
        response.raise_for_status()
        data = response.json()
        models = [model["name"] for model in data.get("models", [])]
        return {
            "ollama_running": True,
            "available_models": models,
            "chat_model_ready": any(self.chat_model in model for model in models),
            "embed_model_ready": any(self.embed_model in model for model in models),
            "keep_alive": self.keep_alive,
        }


class LLMClient:
    """Application facade backed exclusively by Ollama."""

    def __init__(self) -> None:
        self._ollama = OllamaProvider()

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def startup(self) -> None:
        await self._ollama.startup()

    async def shutdown(self) -> None:
        await self._ollama.shutdown()

    async def warmup(self) -> None:
        """Load embedding and chat models before the first guest request."""
        await self._ollama.embed("Chatbot VietSage")
        await self._ollama.chat(
            [{"role": "user", "content": "Chỉ trả lời đúng một từ: OK"}]
        )

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Remove reasoning tags and their contents from a completed reply."""
        if "</think>" in text and "<think>" not in text:
            text = text.rsplit("</think>", 1)[-1]
        text = re.sub(r"<think>[\s\S]*?</think>\s*", "", text)
        text = re.sub(r"<think>[\s\S]*", "", text)
        text = re.sub(r"</?think>", "", text)
        return text.strip()

    async def chat(
        self,
        messages: list[dict],
        *,
        num_predict: int | None = None,
        num_ctx: int | None = None,
    ) -> str:
        usage: dict = {}
        _LAST_CHAT_USAGE.set(None)
        usage_token = _ACTIVE_CHAT_USAGE.set(usage)
        options = {
            key: value
            for key, value in (("num_predict", num_predict), ("num_ctx", num_ctx))
            if value is not None
        }
        options_token = _ACTIVE_CHAT_OPTIONS.set(options)
        try:
            cleaned = self._strip_thinking(await self._ollama.chat(messages))
            if not cleaned:
                raise RuntimeError("LLM returned an empty response.")
            return cleaned
        finally:
            _LAST_CHAT_USAGE.set(dict(usage))
            _ACTIVE_CHAT_OPTIONS.reset(options_token)
            _ACTIVE_CHAT_USAGE.reset(usage_token)

    async def chat_structured(
        self,
        messages: list[dict],
        *,
        schema: dict,
        num_predict: int | None = None,
        num_ctx: int | None = None,
    ) -> dict:
        """Return one provider JSON object for Pydantic validation."""
        usage: dict = {}
        _LAST_CHAT_USAGE.set(None)
        usage_token = _ACTIVE_CHAT_USAGE.set(usage)
        options_token = _ACTIVE_CHAT_OPTIONS.set(
            {
                key: value
                for key, value in (("num_predict", num_predict), ("num_ctx", num_ctx))
                if value is not None
            }
        )
        try:
            return await self._ollama.chat_json(
                messages,
                schema=schema,
                num_predict=num_predict,
                num_ctx=num_ctx,
            )
        finally:
            _LAST_CHAT_USAGE.set(dict(usage))
            _ACTIVE_CHAT_OPTIONS.reset(options_token)
            _ACTIVE_CHAT_USAGE.reset(usage_token)

    async def chat_stream(
        self,
        messages: list[dict],
        *,
        num_predict: int | None = None,
        num_ctx: int | None = None,
    ) -> AsyncGenerator[str, None]:
        usage: dict = {}
        _LAST_CHAT_USAGE.set(None)
        usage_token = _ACTIVE_CHAT_USAGE.set(usage)
        options = {
            key: value
            for key, value in (("num_predict", num_predict), ("num_ctx", num_ctx))
            if value is not None
        }
        options_token = _ACTIVE_CHAT_OPTIONS.set(options)
        provider_stream = self._ollama.chat_stream(messages)
        visible_stream = strip_thinking_stream(provider_stream)
        try:
            emitted = False
            async for chunk in visible_stream:
                emitted = True
                yield chunk
            if not emitted:
                raise RuntimeError("LLM returned an empty response.")
        finally:
            await visible_stream.aclose()
            _LAST_CHAT_USAGE.set(dict(usage))
            _ACTIVE_CHAT_OPTIONS.reset(options_token)
            _ACTIVE_CHAT_USAGE.reset(usage_token)

    async def embed(self, text: str) -> list[float]:
        return await self._ollama.embed(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return await self._ollama.embed_batch(texts)

    async def health_check(self) -> dict:
        return await self._ollama.health_check()


llm_client = LLMClient()
ollama_client = llm_client
