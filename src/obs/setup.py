"""Process telemetry setup (D-56): the catalog-bound instruments and tracer used by the edges.

``instruments()`` and ``tracer()`` are cached per process (the SDK providers are the only global
state, O1). ``configure(exporter, resource)`` installs a ``TracerProvider`` with the scrubbing
processor and a ``BoundedExporter`` around the given exporter; without it OpenTelemetry's no-op
providers are used and nothing is exported (production needs the OTLP or Cloud Trace exporter:
[VERIFY] V-OBS-1). ``safe`` wraps any telemetry call so its failure never reaches the caller.

GUARANTEE(test_telemetry_failures_never_reach_the_caller): O1.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Any, TypeVar

from opentelemetry import metrics, trace
from opentelemetry.sdk.trace.export import SpanExporter

from obs.catalog import load_catalog
from obs.exporters import BoundedExporter, DropCounter
from obs.metrics import Instruments
from obs.tracing import provider

T = TypeVar("T")


@lru_cache(maxsize=1)
def drops() -> DropCounter:
    """The process drop counter (read by the watchdog)."""
    return DropCounter()


@lru_cache(maxsize=1)
def instruments() -> Instruments:
    """Catalog-bound instruments on the global meter."""
    return Instruments(load_catalog(), metrics.get_meter("ai"), drops())


def tracer() -> trace.Tracer:
    """The process tracer."""
    return trace.get_tracer("ai")


def configure(
    exporter: SpanExporter, resource: dict[str, str], max_queue: int = 2048
) -> BoundedExporter:
    """Install the provider; returns the bounded exporter (flushed by a background job)."""
    bounded = BoundedExporter(exporter, max_queue, drops())
    trace.set_tracer_provider(provider(load_catalog(), drops(), bounded, resource))
    return bounded


def safe(fn: Callable[[], T], default: T) -> T:
    """Run a telemetry call; any failure is a counted drop, never an error for the caller."""
    try:
        return fn()
    except Exception:
        # telemetry must never raise into the caller: any failure is a counted drop
        drops().add("metrics")
        return default


def record(name: str, value: float, labels: dict[str, Any] | None = None) -> None:
    """Record a catalog metric safely."""
    safe(lambda: instruments().record(name, value, labels), None)
