"""Application metrics to Cloud Monitoring (D-56, audit 2026-10-15): the catalog instruments
(``obs.setup.instruments``) were recorded on OpenTelemetry's no-op provider, so the generated
dashboards and alerts on ``workload.googleapis.com/*`` had no data.

``install(project, service)`` sets the global ``MeterProvider`` once per process with a periodic
reader (``EXPORT_INTERVAL_S``) and the Google Cloud Monitoring exporter; every catalog metric is
written as ``workload.googleapis.com/<name>``. The monitored resource comes from the Cloud Run
detector (one series per instance, so instances never overwrite each other's points). Gauges
are exported only in the interval they were set (delta), so a value one instance set minutes ago
is never repeated as current (the stuck-run gauge and the heartbeat depend on it).

The Google exporter is deprecated upstream in favour of OTLP to the Telemetry API, which names
metrics differently (``prometheus.googleapis.com``); it is kept until the dashboards and alerts
move with it.

The exporter runs on its own thread and its failures are logged by the SDK, never raised into a
request (O1). ``flush`` is for the scheduled metrics task: an api instance's CPU is throttled
between requests, so the task hands its points over before it returns.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

EXPORT_INTERVAL_S = 60
FLUSH_TIMEOUT_MS = 10_000

log = logging.getLogger(__name__)
_provider: Any = None


def install(project: str, service: str) -> bool:
    """Install the exporting provider (idempotent); False when the exporter cannot start."""
    global _provider
    if _provider is not None:
        return True
    try:
        import warnings

        from opentelemetry import metrics

        warnings.filterwarnings("ignore", "CloudMonitoringMetricsExporter is deprecated")
        from opentelemetry.exporter.cloud_monitoring import CloudMonitoringMetricsExporter
        from opentelemetry.resourcedetector.gcp_resource_detector import (
            GoogleCloudResourceDetector,
        )
        from opentelemetry.sdk.metrics import MeterProvider, _Gauge
        from opentelemetry.sdk.metrics.export import (
            AggregationTemporality,
            PeriodicExportingMetricReader,
        )
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service}).merge(
            GoogleCloudResourceDetector(raise_on_error=False).detect()
        )
        exporter = CloudMonitoringMetricsExporter(project_id=project)
        # the reader takes its temporality from the exporter; everything else stays cumulative
        exporter._preferred_temporality = {_Gauge: AggregationTemporality.DELTA}
        reader = PeriodicExportingMetricReader(
            exporter, export_interval_millis=EXPORT_INTERVAL_S * 1000
        )
        provider = MeterProvider(metric_readers=[reader], resource=resource)
        metrics.set_meter_provider(provider)
    except Exception as error:  # noqa: BLE001 - no metrics never stops the service
        log.warning("metrics_export_unavailable", extra={"error_class": type(error).__name__})
        return False
    _provider = provider
    return True


async def flush() -> bool:
    """Export what has been recorded now (off the event loop); False without an exporter."""
    if _provider is None:
        return False
    try:
        return bool(await asyncio.to_thread(_provider.force_flush, FLUSH_TIMEOUT_MS))
    except Exception:  # noqa: BLE001 - a failed export is telemetry's problem, not the task's
        return False
