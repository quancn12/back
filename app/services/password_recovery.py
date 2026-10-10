"""Delivery boundary for a future password-recovery implementation.

No adapter is configured today, so the public capability remains false and no
reset endpoint claims that mail was sent. A future implementation must create
and persist hashed, expiring, single-use tokens before invoking this boundary.
"""
from __future__ import annotations

from typing import Protocol


class PasswordRecoveryDelivery(Protocol):
    @property
    def is_configured(self) -> bool: ...

    async def send_reset_link(self, *, email: str, reset_url: str) -> None: ...

