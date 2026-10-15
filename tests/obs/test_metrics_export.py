"""App metrics to Cloud Monitoring and the PromQL alerts on them (audit 2026-10-15): counters are
alerted on their increase over a window (never their running total), the availability burn is a
ratio over both windows, a paused alert is left out, and the exporter installs once, sends the
catalog names as ``workload.googleapis.com/<name>`` and never fails the service."""

from __future__ import annotations

from pathlib import Path

import pytest

from obs import alerts, export
from obs.catalog import load_catalog

ROOT = Path(__file__).resolve().parents[2]
TEL = ROOT / "config/telemetry"
LOADED = alerts.load(load_catalog(), TEL, ROOT / "docs/runbooks")
POLICIES = alerts.terraform(LOADED)["resource"]["google_monitoring_alert_policy"]


def _query(name: str) -> str:
    return str(POLICIES[name]["conditions"][0]["condition_prometheus_query_language"]["query"])


def test_counters_are_alerted_on_their_increase_never_their_total() -> None:
    for name, alert in LOADED.items():
        if alert.kind in ("any", "rate_above") and name in POLICIES:
            assert _query(name).startswith("sum(increase("), name
    assert _query("provider_failures") == (
        'sum(increase(workload_googleapis_com:connector_calls{outcome!~"done|replayed|prepulled"}'
        "[900s])) > 20"
    )


def test_the_availability_burn_is_an_error_ratio_over_both_windows() -> None:
    q = _query("api_availability_fast_burn")
    assert q.count('code_class=~"5xx|timeout"') == 2 and "[1h]" in q and "[5m]" in q
    assert "> 0.0144" in q and ">= 100" in q  # 14.4 x the 0.1% budget, with a minimum volume


def test_thresholds_hold_for_their_duration_and_latency_uses_the_p99() -> None:
    stuck = POLICIES["stuck_queued"]["conditions"][0]["condition_prometheus_query_language"]
    assert stuck["duration"] == "300s" and 'state="QUEUED"' in stuck["query"]
    assert _query("api_latency_p99").startswith("histogram_quantile(0.99,")
    assert _query("telemetry_down") == "absent_over_time(workload_googleapis_com:heartbeat[600s])"


def test_a_paused_alert_is_not_created() -> None:
    assert LOADED["refresh_scheduler_stalled"].spec.get("paused")
    assert "refresh_scheduler_stalled" not in POLICIES
    assert len(POLICIES) == len(LOADED) - 1


def test_the_exporter_installs_once_and_names_metrics_for_workload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentelemetry.exporter.cloud_monitoring import CloudMonitoringMetricsExporter
    from opentelemetry.sdk.metrics.export import AggregationTemporality
    from opentelemetry.sdk.resources import Resource

    made: list[dict[str, object]] = []

    class Client:  # no network: the Monitoring client is never called in this test
        @staticmethod
        def common_project_path(project: str) -> str:
            return f"projects/{project}"

    class Detector:  # off Google Cloud the real one waits 30 s for the metadata server
        def __init__(self, raise_on_error: bool = False) -> None:
            pass

        def detect(self) -> Resource:
            return Resource.get_empty()

    class Recording(CloudMonitoringMetricsExporter):
        def __init__(self, **kw: object) -> None:
            made.append(kw)
            super().__init__(**kw, client=Client())  # type: ignore[arg-type]

    monkeypatch.setattr(
        "opentelemetry.exporter.cloud_monitoring.CloudMonitoringMetricsExporter", Recording
    )
    monkeypatch.setattr(
        "opentelemetry.resourcedetector.gcp_resource_detector.GoogleCloudResourceDetector", Detector
    )
    monkeypatch.setattr("opentelemetry.metrics.set_meter_provider", lambda p: None)
    monkeypatch.setattr(export, "_provider", None)
    assert export.install("demo-project", "ai-api") is True
    assert export.install("demo-project", "ai-api") is True  # idempotent
    assert made == [{"project_id": "demo-project"}]
    assert Recording(project_id="x")._prefix == "workload.googleapis.com"
    reader = export._provider._metric_readers[0]
    by_name = {k.__name__: v for k, v in reader._instrument_class_temporality.items()}
    assert by_name["_Gauge"] is AggregationTemporality.DELTA  # a gauge is sent once per set
    assert by_name["_Counter"] is AggregationTemporality.CUMULATIVE
    export._provider.shutdown()
    monkeypatch.setattr(export, "_provider", None)


def test_an_exporter_that_cannot_start_never_stops_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(**_: object) -> None:
        raise RuntimeError("no credentials")

    monkeypatch.setattr(
        "opentelemetry.exporter.cloud_monitoring.CloudMonitoringMetricsExporter", broken
    )
    monkeypatch.setattr(export, "_provider", None)
    assert export.install("demo-project", "ai-api") is False
    assert export._provider is None


@pytest.mark.asyncio
async def test_flush_without_an_exporter_is_a_quiet_no(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export, "_provider", None)
    assert await export.flush() is False
