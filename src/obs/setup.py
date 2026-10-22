"""Process telemetry setup (D-56): the catalog-bound instruments and tracer used by the edges.

``instruments()`` and ``tracer()`` are cached per process (the SDK providers are the only global
state, O1). ``configure(exporter, resource)`` installs a ``TracerProvider`` with the scrubbing
processor and a ``BoundedExporter`` around the given exporter; without it OpenTelemetry's no-op
providers are used and nothing is exported. The cloud profiles call ``install_cloud_trace``
from the API lifespan (next to the metrics exporter, ``obs.export``): it configures the Cloud
Trace exporter once per process and ``trace_flush_loop`` drains the bounded queue off the event
loop; hermetic and local profiles never call it, so they stay on the no-op provider (V-OBS-1
resolved 2026-10-22). ``safe`` wraps any telemetry call so its failure never reaches the caller.

GUARANTEE(test_telemetry_failures_never_reach_the_caller): O1.
"""

from __future__ import annotations

import asyncio
import logging
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
TRACE_FLUSH_INTERVAL_S = 15
_log = logging.getLogger(__name__)
_cloud_trace: BoundedExporter | None = None


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


def install_cloud_trace(project: str, service: str) -> BoundedExporter | None:
    """Install the Cloud Trace exporter once per process (cloud profiles only); None when the
    exporter cannot be built. Never raises: no traces never stops the service (O1)."""
    global _cloud_trace
    if _cloud_trace is not None:
        return _cloud_trace
    try:
        from obs.exporters import cloud_trace_exporter

        _cloud_trace = configure(cloud_trace_exporter(project), {"service.name": service})
    except Exception as error:  # noqa: BLE001 - telemetry setup must not stop the service
        _log.warning("trace_export_unavailable", extra={"error_class": type(error).__name__})
        return None
    return _cloud_trace


async def flush_traces() -> int:
    """Hand the queued spans to Cloud Trace off the event loop; 0 without an exporter."""
    if _cloud_trace is None:
        return 0
    try:
        return int(await asyncio.to_thread(_cloud_trace.flush))
    except Exception:  # noqa: BLE001 - a failed export is telemetry's problem, never the app's
        drops().add("traces")
        return 0


async def trace_flush_loop(interval_s: float = TRACE_FLUSH_INTERVAL_S) -> None:
    """Drain the bounded span queue every ``interval_s`` for the life of the process."""
    # LOOP(trace export drain loop): cap=none, one flush per interval for the life of the
    # service; exit=task cancelled at shutdown (the lifespan flushes once more)
    while True:
        await asyncio.sleep(max(1.0, float(interval_s)))
        await flush_traces()


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
