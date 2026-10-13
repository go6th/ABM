"""The id of the request the current code runs for (a UUID string; None outside a request).

Set by the API middleware; asyncio tasks started inside a request inherit it, so the worker hop
and the log lines of that work carry the same id. Lives in the platform layer so adapters can
read it without importing observability (O1); ``obs.logging`` re-exports it.
"""

from __future__ import annotations

from contextvars import ContextVar

REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
