"""Cooperative cancellation token (P9).

A slice deadline, a run cancel or a pause sets the token once with its reason; every in-flight unit
checks it between provider calls (``raise_if_set``) and the executor stops launching. Nothing is
killed mid-call: a paid call either completes and is recorded by the connector or leaves the
ledger's ``started`` intent for reconciliation. The first reason wins (idempotent).

GUARANTEE(test_cancel_token_first_reason_wins): later reasons do not overwrite the first.
"""

from __future__ import annotations

import asyncio

from parallel.errors import Cancelled


class CancelToken:
    """Set once; awaitable; checked between calls."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None

    @property
    def reason(self) -> str | None:
        """Why the token fired, or None."""
        return self._reason

    def is_set(self) -> bool:
        """Whether the token has fired."""
        return self._event.is_set()

    def cancel(self, reason: str) -> None:
        """Fire the token (the first reason is kept)."""
        if self._reason is None:
            self._reason = reason
        self._event.set()

    def raise_if_set(self) -> None:
        """``Cancelled`` when the token has fired."""
        if self._reason is not None:
            raise Cancelled(self._reason)

    async def wait(self) -> str:
        """Block until the token fires; returns the reason."""
        await self._event.wait()
        assert self._reason is not None
        return self._reason
