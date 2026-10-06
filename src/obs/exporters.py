"""Exporters that can never change behaviour (O1).

``BoundedExporter`` wraps any span exporter: ``export`` only enqueues into a bounded deque (drop
oldest, counted per signal) and returns at once; ``flush`` drains with a time budget and swallows
every exporter error (counted). A down collector, a full queue, a hanging exporter or a raising
exporter leaves runs, the API and settlement untouched. ``DropCounter`` is the
``telemetry_dropped_total`` state, readable by the watchdog without the pipeline.

GUARANTEE(test_telemetry_failures_never_reach_the_caller): chaos (O1).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


@dataclass
class DropCounter:
    """Dropped telemetry by signal (traces, metrics, logs)."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, signal: str, n: int = 1) -> None:
        """Count drops."""
        self.counts[signal] = self.counts.get(signal, 0) + n


class BoundedExporter(SpanExporter):
    """Non-blocking, bounded, failure-swallowing wrapper (see the module docstring)."""

    def __init__(
        self,
        inner: SpanExporter,
        max_queue: int,
        drops: DropCounter,
        budget_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.inner, self.drops, self.budget_s, self.clock = inner, drops, budget_s, clock
        self.queue: deque[ReadableSpan] = deque(maxlen=max_queue)
        self.failures = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Enqueue only (drop oldest when full); never blocks, never raises."""
        for span in spans:
            if len(self.queue) == self.queue.maxlen:
                self.drops.add("traces")
            self.queue.append(span)
        return SpanExportResult.SUCCESS

    def flush(self, batch: int = 512) -> int:
        """Drain within the time budget; returns spans handed to the inner exporter."""
        sent = 0
        start = self.clock()
        # LOOP(telemetry export drain): cap=queue length and budget_s; exit=empty, budget,
        # exporter failure (counted, the batch dropped)
        while self.queue and self.clock() - start < self.budget_s:
            chunk = [self.queue.popleft() for _ in range(min(batch, len(self.queue)))]
            try:
                result = self.inner.export(chunk)
            except Exception:  # an exporter defect is telemetry's problem, never the caller's
                result = SpanExportResult.FAILURE
            if result is not SpanExportResult.SUCCESS:
                self.failures += 1
                self.drops.add("traces", len(chunk))
                continue
            sent += len(chunk)
        return sent

    def shutdown(self) -> None:
        """Best-effort flush."""
        self.flush()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flush within the budget."""
        self.flush()
        return not self.queue


class CollectingExporter(SpanExporter):
    """In-memory exporter (tests and the canary scan)."""

    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Keep the spans."""
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """Nothing to release."""


def otlp_exporter(endpoint: str) -> Any:
    """The OTLP exporter (D-56). Not installed in this image: [VERIFY] V-OBS-1."""
    from obs.errors import NotConfigured

    raise NotConfigured(f"OTLP exporter for {endpoint} is not installed")
