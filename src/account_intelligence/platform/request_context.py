"""Per-request correlation context shared by API edges, task schedulers and log formatters."""

from __future__ import annotations

from contextvars import ContextVar

REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
