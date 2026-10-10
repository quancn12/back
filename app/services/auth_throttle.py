"""Small central auth-throttling hook for the single-process MVP.

This deliberately makes no distributed-rate-limit claim. Production multi-worker
deployments must replace the store with a shared backend or enforce the same
policy at the trusted edge.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException

from app.core.config import settings


class InProcessAuthThrottle:
    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _active(self, key: str, now: float) -> deque[float]:
        values = self._failures[key]
        cutoff = now - settings.auth_login_window_seconds
        while values and values[0] <= cutoff:
            values.popleft()
        return values

    def check(self, key: str) -> None:
        with self._lock:
            if len(self._active(key, time.monotonic())) >= settings.auth_login_max_attempts:
                raise HTTPException(
                    status_code=429,
                    detail="Too many authentication attempts. Try again later.",
                )

    def failure(self, key: str) -> None:
        with self._lock:
            self._active(key, time.monotonic()).append(time.monotonic())

    def success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


auth_throttle = InProcessAuthThrottle()
