"""Optional JSONL metrics for chat latency and SSE completion status."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import AsyncGenerator

from app.core.config import settings
from app.services.llm_client import current_chat_usage


_logger = logging.getLogger("vietsage.chat_metrics")
_logger.setLevel(logging.INFO)
_logger.propagate = False
_telemetry_logger = logging.getLogger("vietsage.chat_telemetry")
_telemetry_logger.setLevel(logging.INFO)
_telemetry_logger.propagate = False
_log_maintenance_lock = threading.RLock()
_last_retention_check = 0.0
_RETENTION_CHECK_SECONDS = 3600.0


def _active_model() -> str:
    return settings.ollama_chat_model


def _write_to(logger: logging.Logger, filename: str, record: dict) -> None:
    with _log_maintenance_lock:
        _maybe_prune_chat_logs()
        if not logger.handlers:
            log_dir = Path(settings.chat_log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_dir / filename,
                maxBytes=10_000_000,
                backupCount=5,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        logger.info(json.dumps(record, ensure_ascii=False))


def _write(record: dict) -> None:
    _write_to(_logger, "chat-performance.jsonl", record)


def _write_telemetry(record: dict) -> None:
    _write_to(_telemetry_logger, "chat-telemetry.jsonl", record)


def _record_timestamp(record: dict) -> datetime | None:
    value = record.get("timestamp_utc")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    if not path.is_file():
        return records
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _close_log_handlers() -> None:
    for logger in (_logger, _telemetry_logger):
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def prune_chat_logs(*, now: datetime | None = None) -> dict[str, int]:
    """Apply the configured time-retention rule to JSONL chat logs.

    This runs before log handlers open at process startup, which keeps Windows
    file replacement safe. Malformed/undated rows are dropped: data that cannot
    prove it is still inside the retention window must not be retained.
    """

    global _last_retention_check
    with _log_maintenance_lock:
        _close_log_handlers()
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = current - timedelta(days=settings.chat_log_retention_days)
        removed = kept = files = 0
        log_dir = Path(settings.chat_log_dir)
        if log_dir.is_dir():
            for pattern in ("chat-performance.jsonl*", "chat-telemetry.jsonl*"):
                for path in log_dir.glob(pattern):
                    if not path.is_file():
                        continue
                    files += 1
                    retained: list[str] = []
                    source_lines = path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                    for line in source_lines:
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            removed += 1
                            continue
                        timestamp = _record_timestamp(record) if isinstance(record, dict) else None
                        if timestamp is None or timestamp < cutoff:
                            removed += 1
                            continue
                        retained.append(json.dumps(record, ensure_ascii=False))
                        kept += 1
                    path.write_text(
                        "".join(f"{line}\n" for line in retained), encoding="utf-8"
                    )
        _last_retention_check = time.monotonic()
        return {"files": files, "kept": kept, "removed": removed}


def _maybe_prune_chat_logs() -> None:
    if time.monotonic() - _last_retention_check >= _RETENTION_CHECK_SECONDS:
        prune_chat_logs()


def summarize_chat_telemetry() -> dict[str, object]:
    """Aggregate metadata-only telemetry without returning individual rows."""

    log_dir = Path(settings.chat_log_dir)
    records: list[dict] = []
    if log_dir.is_dir():
        for path in sorted(log_dir.glob("chat-telemetry.jsonl*")):
            records.extend(_read_jsonl(path))

    statuses: dict[str, int] = {}
    routes: dict[str, int] = {}
    timestamps: list[datetime] = []
    for record in records:
        status = str(record.get("status") or "unknown")
        route = str(record.get("route") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        routes[route] = routes.get(route, 0) + 1
        timestamp = _record_timestamp(record)
        if timestamp is not None:
            timestamps.append(timestamp)

    def average(field: str) -> float | None:
        values = [
            float(record[field])
            for record in records
            if isinstance(record.get(field), (int, float))
        ]
        return round(sum(values) / len(values), 2) if values else None

    return {
        "enabled": settings.chat_telemetry_enabled,
        "retention_days": settings.chat_log_retention_days,
        "records": len(records),
        "statuses": statuses,
        "routes": routes,
        "average_ms": {
            "prepare": average("rag_prepare_ms"),
            "first_content": average("first_content_ms"),
            "total": average("total_ms"),
        },
        "average_response_chars": average("response_chars"),
        "average_source_count": average("source_count"),
        "last_event_utc": max(timestamps).isoformat() if timestamps else None,
    }


def _parse_sse(event: str) -> tuple[str, dict]:
    event_name = ""
    data_text = ""
    for line in event.splitlines():
        if line.startswith("event: "):
            event_name = line[7:].strip()
        elif line.startswith("data: "):
            data_text = line[6:].strip()
    try:
        payload = json.loads(data_text) if data_text else {}
    except json.JSONDecodeError:
        payload = {}
    return event_name, payload if isinstance(payload, dict) else {}


async def observe_sse_stream(
    stream: AsyncGenerator[str, None],
    *,
    started: float,
    prepared: float,
    scope: str,
    route: str,
    question: str,
    session_id: str,
    retrieved_chunk_count: int = 0,
) -> AsyncGenerator[str, None]:
    """Pass through an SSE stream and optionally record server-side timings.

    ``CHAT_LOG_ENABLED`` retains the historical content-bearing record. The
    separate ``CHAT_TELEMETRY_ENABLED`` path writes timing and provider counters
    only; it never writes question, answer, session, scope, source text, or
    error detail.
    """
    content_logging = settings.chat_log_enabled
    telemetry_enabled = settings.chat_telemetry_enabled
    if not content_logging and not telemetry_enabled:
        try:
            async for event in stream:
                yield event
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
        return

    request_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    record = (
        {
            "request_id": request_id,
            "timestamp_utc": timestamp,
            "route": route,
            "scope": scope,
            "session_id": session_id,
            "question": question,
            "provider": "ollama",
            "model": _active_model(),
            "rag_prepare_ms": round((prepared - started) * 1000, 2),
            "first_content_ms": None,
            "status": "interrupted",
            "response": "",
            "stream_chunks": 0,
            "source_count": retrieved_chunk_count,
        }
        if content_logging
        else None
    )
    telemetry = (
        {
            "request_id": request_id,
            "timestamp_utc": timestamp,
            "route": route,
            "provider": "ollama",
            "model": _active_model(),
            "rag_prepare_ms": round((prepared - started) * 1000, 2),
            "question_chars": len(question),
            "first_content_ms": None,
            "status": "interrupted",
            "response_chars": 0,
            "stream_chunks": 0,
            "source_count": retrieved_chunk_count,
        }
        if telemetry_enabled
        else None
    )

    def note_first_content() -> None:
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        if record is not None and record["first_content_ms"] is None:
            record["first_content_ms"] = elapsed
        if telemetry is not None and telemetry["first_content_ms"] is None:
            telemetry["first_content_ms"] = elapsed

    try:
        async for event in stream:
            event_name, payload = _parse_sse(event)

            if event_name == "token":
                text = payload.get("text", "")
                if isinstance(text, str) and text:
                    note_first_content()
                    if record is not None:
                        record["response"] += text
                        record["stream_chunks"] += 1
                    if telemetry is not None:
                        telemetry["response_chars"] += len(text)
                        telemetry["stream_chunks"] += 1
            elif event_name == "replace":
                text = payload.get("text")
                if isinstance(text, str):
                    if text:
                        note_first_content()
                    if record is not None:
                        record["response"] = text
                    if telemetry is not None:
                        telemetry["response_chars"] = len(text)
            elif event_name == "done":
                if record is not None:
                    record["status"] = "ok"
                if telemetry is not None:
                    telemetry["status"] = "ok"
            elif event_name == "error":
                if record is not None:
                    record["status"] = "error"
                    record["error"] = payload.get("detail", "unknown stream error")
                if telemetry is not None:
                    telemetry["status"] = "error"
            yield event
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
        total_ms = round((time.perf_counter() - started) * 1000, 2)
        if record is not None:
            record["total_ms"] = total_ms
            record["response_chars"] = len(record["response"])
            _write(record)
        if telemetry is not None:
            telemetry["total_ms"] = total_ms
            usage = current_chat_usage()
            if usage:
                telemetry["provider_usage"] = usage
            _write_telemetry(telemetry)
