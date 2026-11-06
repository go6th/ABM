"""OpenTelemetry tracing and metrics for the RAG pipeline.

Observability follows the platform's connector layer:
structured logs through a field allow-list (nothing
outside the list is emitted — prompts, chunk text and
secrets cannot leak through a log call), and spans whose
attributes use the OpenTelemetry semantic-convention
namespaces (``gen_ai.*``, ``rag.*``, ``db.*``) so any
OTel backend can query them.

Anti-patterns the blueprint warns about are avoided by
construction: one span per pipeline phase (never per
line of code, never per retrieved chunk — ``rag.retriever.chunks``
records the count instead), and PII-bearing attributes
(``gen_ai.input.messages``) are never attached; the
collector scrubs what little text metadata ships.
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
        "phase",
        "outcome",
        "error_class",
        "latency_ms",
        "model",
        "prompt_version",
        "tier",
        "top_k",
        "chunks",
        "methods",
        "cache_hit",
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "loop_passes",
        "decision",
        "cost_usd",
        "run_id",
        "tenant_id",
    }
)
SPAN_FIELDS = frozenset(
    {
        "gen_ai.request.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.cache_read_input_tokens",
        "app.prompt.version",
        "rag.retriever.top_k",
        "rag.retriever.chunks",
        "rag.retriever.methods",
        "rag.cache.hit",
        "rag.loop.passes",
        "db.operation.name",
    }
)

_logger = logging.getLogger("account_intelligence.rag")
_tracer = trace.get_tracer("account_intelligence.rag")
_meter = metrics.get_meter("account_intelligence.rag")
_phase_calls = _meter.create_counter("rag_phase_calls", description="RAG phase calls by outcome")
_phase_latency = _meter.create_histogram("rag_phase_latency_ms", unit="ms")
_cache_hits = _meter.create_counter("rag_cache_hits", description="semantic cache hits and misses")


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Structured JSON log through the field allow-list."""
    payload = {"event": event}
    payload.update({key: _scalar(value) for key, value in fields.items() if key in LOG_FIELDS})
    _logger.log(level, json.dumps(payload, sort_keys=True, default=str))


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def record_phase(phase: str, outcome: str, latency_ms: int | None) -> None:
    """Count one phase call and record its latency."""
    _phase_calls.add(1, {"phase": phase, "outcome": outcome})
    if latency_ms is not None:
        _phase_latency.record(latency_ms, {"phase": phase})


def record_cache_hit(hit: bool) -> None:
    """Count one cache lookup by outcome."""
    _cache_hits.add(1, {"outcome": "hit" if hit else "miss"})


def run_trace_context(run_id: UUID) -> Context:
    """A remote parent whose trace id is the run id."""
    span_id = int.from_bytes(hashlib.sha256(run_id.bytes).digest()[:8], "big") or 1
    parent = SpanContext(
        trace_id=run_id.int,
        span_id=span_id,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace.set_span_in_context(NonRecordingSpan(parent))


@contextmanager
def phase_span(run_id: UUID, phase: str, attributes: Mapping[str, Any]) -> Iterator[trace.Span]:
    """One span per pipeline phase, allow-listed attributes only."""
    clean = {
        key: _scalar(value)
        for key, value in attributes.items()
        if value is not None and key in SPAN_FIELDS
    }
    with _tracer.start_as_current_span(
        f"rag.{phase}", context=run_trace_context(run_id), attributes=clean
    ) as span:
        yield span
