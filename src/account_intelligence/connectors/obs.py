"""Tracing, metrics and structured logs for connector calls.

Logs are JSON with a field allow-list: anything not listed is dropped, so payloads,
page text, URLs with queries and secrets cannot leak through a log call. Metric
labels are provider code, operation and outcome only (no tenant label: cardinality,
and tenant ids are not metric data). Spans carry the attributes from the build brief.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

LOG_FIELDS = frozenset(
    {
        "event",
        "provider_code",
        "operation",
        "route",
        "fell_back_from",
        "attempt",
        "outcome",
        "error_class",
        "http_status",
        "latency_ms",
        "bytes",
        "pages",
        "items_read",
        "items_kept",
        "stop_reason",
        "cost_usd",
        "charged_usd",
        "credits",
        "cost_source",
        "possible_duplicate",
        "replayed",
        "run_id",
        "tenant_id",
        "retry_after_s",
        "waited_s",
    }
)

_logger = logging.getLogger("account_intelligence.connectors")
_tracer = trace.get_tracer("account_intelligence.connectors")
_meter = metrics.get_meter("account_intelligence.connectors")
_calls = _meter.create_counter("connector_calls", description="provider calls by outcome")
_unknown_cost = _meter.create_counter(
    "connector_unknown_cost", description="calls whose dollar cost is unknown"
)
_latency = _meter.create_histogram("connector_latency_ms", unit="ms")


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    payload = {"event": event}
    payload.update({k: _scalar(v) for k, v in fields.items() if k in LOG_FIELDS})
    _logger.log(level, json.dumps(payload, sort_keys=True, default=str))


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def record_call(provider_code: str, operation: str, outcome: str, latency_ms: int | None) -> None:
    labels = {"provider_code": provider_code, "operation": operation, "outcome": outcome}
    _calls.add(1, labels)
    if latency_ms is not None:
        _latency.record(latency_ms, labels)


def record_unknown_cost(provider_code: str, operation: str) -> None:
    _unknown_cost.add(1, {"provider_code": provider_code, "operation": operation})


def run_trace_context(run_id: UUID) -> Context:
    """A remote parent whose trace id is the run id, so every span of a run shares it."""
    span_id = int.from_bytes(hashlib.sha256(run_id.bytes).digest()[:8], "big") or 1
    parent = SpanContext(
        trace_id=run_id.int,
        span_id=span_id,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace.set_span_in_context(NonRecordingSpan(parent))


@contextmanager
def call_span(run_id: UUID, attributes: Mapping[str, Any]) -> Iterator[trace.Span]:
    clean = {k: _scalar(v) for k, v in attributes.items() if v is not None and k in SPAN_FIELDS}
    with _tracer.start_as_current_span(
        "connector.call", context=run_trace_context(run_id), attributes=clean
    ) as span:
        yield span


SPAN_FIELDS = frozenset(
    {"provider_code", "operation", "route", "attempt", "cost_usd", "cost_source", "outcome"}
)
