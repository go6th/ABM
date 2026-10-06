"""Tracer setup (D-56, O2, O3): a ``TracerProvider`` whose ids come from ``DeterministicIds`` (a
span id set in a context variable by ``runspans`` is used; otherwise a random one), and whose span
processor scrubs attributes against the span catalog before export. Export goes through
``BoundedExporter`` (O1). Resource attributes carry the service version, graph version and the
catalog hash (O16).

GUARANTEE(test_span_attributes_are_scrubbed_before_export): O3.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.id_generator import IdGenerator, RandomIdGenerator

from obs.catalog import Catalog
from obs.exporters import DropCounter
from obs.scrub import value_ok

NEXT_SPAN_ID: ContextVar[int | None] = ContextVar("next_span_id", default=None)
NEXT_TRACE_ID: ContextVar[int | None] = ContextVar("next_trace_id", default=None)


class DeterministicIds(IdGenerator):
    """Span and trace ids from ``NEXT_SPAN_ID`` and ``NEXT_TRACE_ID`` when set (each consumed
    once), else random."""

    def __init__(self) -> None:
        self._random = RandomIdGenerator()

    def generate_span_id(self) -> int:
        """The planned span id, or a random one."""
        planned = NEXT_SPAN_ID.get()
        if planned is not None:
            NEXT_SPAN_ID.set(None)
            return planned
        return self._random.generate_span_id()

    def generate_trace_id(self) -> int:
        """The planned trace id (a run's root span), or a random one."""
        planned = NEXT_TRACE_ID.get()
        if planned is not None:
            NEXT_TRACE_ID.set(None)
            return planned
        return self._random.generate_trace_id()


class ScrubbingProcessor(SpanProcessor):
    """Drops any attribute the span catalog does not allow for the span's name, or whose value
    has a forbidden shape, before the span reaches the exporter."""

    def __init__(self, catalog: Catalog, drops: DropCounter) -> None:
        self.catalog, self.drops = catalog, drops

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        """Nothing at start."""

    def on_end(self, span: ReadableSpan) -> None:
        """Scrub in place (the SDK's attributes mapping of an ended span)."""
        allowed = self.catalog.spans.get(span.name, frozenset())
        attrs: Any = span._attributes  # noqa: SLF001 - the SDK has no public mutator at end
        if attrs is None:
            return
        for key in list(attrs.keys()):
            if key not in allowed or not value_ok(attrs[key], self.catalog.max_value_chars):
                self.drops.add("traces")
                attrs._dict.pop(key, None)  # noqa: SLF001 - BoundedAttributes storage

    def shutdown(self) -> None:
        """Nothing to release."""

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Nothing buffered."""
        return True


def provider(
    catalog: Catalog, drops: DropCounter, exporter: SpanExporter, resource: dict[str, str]
) -> TracerProvider:
    """A provider wired as in the module docstring (the exporter is wrapped by the caller)."""
    tp = TracerProvider(
        resource=Resource.create({**resource, "catalog_hash": catalog.content_hash}),
        id_generator=DeterministicIds(),
    )
    tp.add_span_processor(ScrubbingProcessor(catalog, drops))
    tp.add_span_processor(SimpleSpanProcessor(exporter))
    return tp
