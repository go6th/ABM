"""Structlog configuration.

Structured JSON logging with a field allow-list; prompts, document text and tokens are
never logged (invariant: no private text in logs; verified by planted canaries in tests).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

ALLOWED_FIELDS = frozenset(
    {
        "event",
        "level",
        "timestamp",
        "run_id",
        "tenant_id",
        "slice_index",
        "tool",
        "status",
        "attempt",
        "node",
        "duration_ms",
    }
)


def _allowlist_processor(
    logger: logging.Logger,
    method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Drop any field not on the allow-list (defence in depth against content leakage)."""
    for key in list(event_dict):
        if key not in ALLOWED_FIELDS:
            event_dict.pop(key, None)
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog for JSON output on stdout."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _allowlist_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
        cache_logger_on_first_use=True,
    )


def get_logger(**initial_context: Any) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger with optional allowed initial context."""
    logger: Any = structlog.get_logger().bind(**initial_context)
    return logger  # type: ignore[no-any-return]
