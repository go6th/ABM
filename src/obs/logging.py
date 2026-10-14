"""Structured JSON logs through the catalog allow-list (O3). ``JsonFormatter`` serialises a record
whose ``extra`` fields pass ``scrub.clean``; the message itself is reduced to its event name;
exceptions become class, code and message hash. The trace id of the current span is attached, and
so is the id of the request being handled (``REQUEST_ID``, set by the API middleware; asyncio
tasks the request starts inherit it, so a log line of that work carries the same id).

GUARANTEE(test_log_lines_carry_only_allowed_fields): canary suite.
"""

from __future__ import annotations

import json
import logging as std_logging
from typing import Any

from opentelemetry import trace

from account_intelligence.platform.request_context import REQUEST_ID
from obs.catalog import Catalog
from obs.exporters import DropCounter
from obs.scrub import clean, scrub_exception

RESERVED = frozenset(vars(std_logging.LogRecord("", 0, "", 0, "", (), None)))
__all__ = ["REQUEST_ID", "JsonFormatter", "configure_logging", "logger"]


class JsonFormatter(std_logging.Formatter):
    """One JSON object per line: allow-listed fields only."""

    def __init__(self, catalog: Catalog, drops: DropCounter, service: str) -> None:
        super().__init__()
        self.catalog, self.drops, self.service = catalog, drops, service

    def format(self, record: std_logging.LogRecord) -> str:
        """Render (never raises: a failure yields a minimal line and a drop)."""
        try:
            extra = {k: v for k, v in vars(record).items() if k not in RESERVED}
            event = str(
                getattr(record, "event", record.msg if isinstance(record.msg, str) else "log")
            )
            payload: dict[str, Any] = {
                "event": event[:64],
                "level": record.levelname,
                # Cloud Logging reads ``severity`` from structured stdout lines
                "severity": record.levelname,
                "service": self.service,
            }
            if record.exc_info and record.exc_info[1] is not None:
                extra.update(scrub_exception(record.exc_info[1]))
            request_id = REQUEST_ID.get()
            if request_id and "request_id" not in extra:
                extra["request_id"] = request_id
            span = trace.get_current_span().get_span_context()
            if span.is_valid:
                extra["trace_id"] = f"{span.trace_id:032x}"
                extra["span_id"] = f"{span.span_id:016x}"
            kept, dropped = clean(extra, self.catalog.log_fields, self.catalog.max_value_chars)
            if dropped:
                self.drops.add("logs", dropped)
            if not set(payload["event"]) <= set("abcdefghijklmnopqrstuvwxyz0123456789._-"):
                payload["event"] = "log"
            payload.update(kept)
            return json.dumps(payload, sort_keys=True, default=str)
        except Exception:
            # a log line must never raise into the caller: count a drop, write a minimal line
            self.drops.add("logs")
            return json.dumps({"event": "log_format_failed", "service": self.service})


def configure_logging(level: str, service: str) -> None:
    """Install the JSON formatter on one stdout handler for the whole process (idempotent)."""
    import sys

    from obs.catalog import load_catalog
    from obs.setup import drops

    handler = std_logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(load_catalog(), drops(), service))
    root = std_logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def logger(name: str) -> std_logging.Logger:
    """A named logger (the only sanctioned way to get one outside this package)."""
    return std_logging.getLogger(name)
