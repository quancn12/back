"""Process-local admission control for public chat work.

The local/ngrok deployment runs one Uvicorn worker, so a small in-process
counter can keep embedding and generation work below the measured machine
capacity. Multi-worker production deployments still need an equivalent shared
or edge-enforced limit; this module deliberately does not claim otherwise.
"""
from __future__ import annotations

import threading
from collections.abc import AsyncIterable, AsyncIterator
from typing import TypeVar

import anyio
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.core.config import settings


_T = TypeVar("_T")


class ChatCapacityLease:
    """One idempotently releasable slot from ``ChatCapacityLimiter``."""

    def __init__(self, limiter: "ChatCapacityLimiter") -> None:
        self._limiter = limiter
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._limiter._release()

    def release_after(self, source: AsyncIterable[_T]) -> AsyncIterator[_T]:
        """Wrap a body iterator so even a pre-first-byte close releases it."""
        return _LeaseReleasingAsyncIterator(source, self)


class _LeaseReleasingAsyncIterator(AsyncIterator[_T]):
    def __init__(
        self, source: AsyncIterable[_T], lease: ChatCapacityLease
    ) -> None:
        self._source = source.__aiter__()
        self._lease = lease

    def __aiter__(self) -> "_LeaseReleasingAsyncIterator[_T]":
        return self

    async def __anext__(self) -> _T:
        try:
            return await self._source.__anext__()
        except BaseException:
            self._lease.release()
            raise

    async def aclose(self) -> None:
        try:
            close = getattr(self._source, "aclose", None)
            if close is not None:
                await close()
        finally:
            self._lease.release()


class CapacityStreamingResponse(StreamingResponse):
    """StreamingResponse that owns and releases a chat-capacity lease.

    The response-level ``finally`` covers ASGI disconnects that happen before
    Starlette asks the body iterator for its first item. The iterator wrapper
    separately covers direct consumers and normal completion; release is
    idempotent so both lifecycle boundaries are safe.
    """

    def __init__(
        self,
        content: AsyncIterable[bytes | str],
        *,
        lease: ChatCapacityLease,
        **kwargs,
    ) -> None:
        self._capacity_lease = lease
        super().__init__(lease.release_after(content), **kwargs)

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                close = getattr(self.body_iterator, "aclose", None)
                if close is not None:
                    # Starlette cancels its stream task on an ASGI 2.3
                    # disconnect. Shield provider/metrics cleanup so returning
                    # the capacity slot never leaves their iterators alive.
                    with anyio.CancelScope(shield=True):
                        await close()
            finally:
                self._capacity_lease.release()


class ChatCapacityLimiter:
    def __init__(self) -> None:
        self._active = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> ChatCapacityLease:
        """Take a slot immediately or fail before retrieval/model work starts."""
        with self._lock:
            if self._active >= settings.chat_max_concurrent_requests:
                raise HTTPException(
                    status_code=503,
                    detail="VietSage đang phục vụ tối đa số lượt chat cùng lúc. Vui lòng thử lại sau.",
                    headers={"Retry-After": str(settings.chat_retry_after_seconds)},
                )
            self._active += 1
        return ChatCapacityLease(self)

    def _release(self) -> None:
        with self._lock:
            # Leases are idempotent, so this guard is only defensive against a
            # future programming error rather than part of normal control flow.
            if self._active > 0:
                self._active -= 1

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active


chat_capacity = ChatCapacityLimiter()
