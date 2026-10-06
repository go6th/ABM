"""Run span helpers (O2): stage, unit, model and tool spans with deterministic ids under the run's
trace (remote parent: the run id is the trace id), and the run's root span emitted exactly once at
terminal settlement (or by the closer for a dead worker), with start = ``created_at`` and end =
the terminal time. Attributes go through the span catalog (scrubbed again by the processor).

GUARANTEE(test_resume_never_duplicates_span_ids): O2.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from obs.context import span_id_for, trace_id_for
from obs.tracing import NEXT_SPAN_ID, NEXT_TRACE_ID

ROOT = "root"


def run_context(run_id: UUID | str, parent_span: int | None = None) -> Any:
    """A remote parent carrying the run's trace id (and the root span's id by default)."""
    ctx = SpanContext(
        trace_id=trace_id_for(run_id),
        span_id=parent_span or span_id_for(run_id, ROOT),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace.set_span_in_context(NonRecordingSpan(ctx))


@contextmanager
def span(
    tracer: trace.Tracer,
    name: str,
    run_id: UUID | str,
    key: tuple[object, ...],
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[trace.Span]:
    """A child of the run root with the deterministic id ``span_id_for(run, name, *key)``."""
    NEXT_SPAN_ID.set(span_id_for(run_id, name, *key))
    with tracer.start_as_current_span(
        name, context=run_context(run_id), attributes=dict(attributes or {})
    ) as s:
        yield s


def emit_root(
    tracer: trace.Tracer,
    run_id: UUID | str,
    created_at: datetime,
    ended_at: datetime,
    attributes: Mapping[str, Any],
) -> None:
    """The run root span, once: its id is ``span_id_for(run, root)`` and its trace id the run's;
    emitting it twice yields the same ids (a sink deduplicates by id); it has no parent."""
    NEXT_SPAN_ID.set(span_id_for(run_id, ROOT))
    NEXT_TRACE_ID.set(trace_id_for(run_id))
    s = tracer.start_span(
        "run",
        context=Context(),  # a true root: no parent, the run's own trace id
        attributes=dict(attributes),
        start_time=int(created_at.timestamp() * 1e9),
    )
    s.end(end_time=int(ended_at.timestamp() * 1e9))
