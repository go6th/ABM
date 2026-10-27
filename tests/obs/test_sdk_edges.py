"""ObsGuard at the SDK edge (in-memory exporters and readers, synthetic data): telemetry failures
never reach the caller (O1); attributes are scrubbed before export (O3); a resume re-emits the same
span ids and the run root is a true root carrying the run's trace id (O2); undeclared labels are
dropped (O5); log lines carry only allowed fields and never a canary or a message (O3)."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from obs import runspans, setup
from obs.catalog import load_catalog
from obs.context import span_id_for, trace_id_for
from obs.exporters import BoundedExporter, CollectingExporter, DropCounter
from obs.logging import JsonFormatter
from obs.metrics import Instruments
from obs.tracing import provider

CAT = load_catalog()
T0 = datetime(2026, 1, 1, tzinfo=UTC)
CANARY = "CANARY-7f3a9e"


def traced() -> tuple[trace.Tracer, CollectingExporter, DropCounter]:
    sink, drops = CollectingExporter(), DropCounter()
    tp = provider(CAT, drops, sink, {"service.name": "test"})
    return tp.get_tracer("t"), sink, drops


class Raising(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise RuntimeError("collector down")

    def shutdown(self) -> None:
        pass


class Slow(SpanExporter):
    """Each export advances a fake clock by a second (a hanging collector)."""

    def __init__(self, clock: list[float]) -> None:
        self.clock = clock

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.clock[0] += 1.0
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


def test_telemetry_failures_never_reach_the_caller() -> None:
    tracer, sink, _ = traced()
    with tracer.start_as_current_span("stage", attributes={"stage": "plan"}):
        pass
    span = sink.spans[0]
    drops = DropCounter()
    raising = BoundedExporter(Raising(), max_queue=3, drops=drops)
    for _ in range(5):  # a full queue drops the oldest and counts it; export never blocks
        assert raising.export([span]) is SpanExportResult.SUCCESS
    assert len(raising.queue) == 3 and drops.counts["traces"] == 2
    assert raising.flush(batch=1) == 0  # every batch fails: swallowed, counted
    assert raising.failures == 3 and drops.counts["traces"] == 5 and not raising.queue
    clock = [0.0]
    slow = BoundedExporter(Slow(clock), 100, DropCounter(), budget_s=2.5, clock=lambda: clock[0])
    slow.export([span] * 10)
    assert slow.flush(batch=1) == 3 and len(slow.queue) == 7  # the time budget bounds a hang
    # a failing instrument or tracer is a counted drop, never an error for the caller
    before = setup.drops().counts.get("metrics", 0)
    assert setup.safe(lambda: 1 / 0, "fallback") == "fallback"
    assert setup.drops().counts["metrics"] == before + 1


@pytest.mark.asyncio
async def test_api_answers_when_telemetry_is_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    from account_intelligence.api.main import create_app

    def broken() -> Instruments:
        raise RuntimeError("meter provider exploded")

    monkeypatch.setattr(setup, "instruments", broken)
    app = create_app()
    app.state.draining = False
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        res = await c.get("/healthz")
    assert res.status_code == 200


def test_span_attributes_are_scrubbed_before_export() -> None:
    tracer, sink, drops = traced()
    with tracer.start_as_current_span(
        "connector.call",
        attributes={
            "provider_code": "p_search",
            "outcome": "ok",
            "url": "https://x.example/?q=acme",  # not allowed for this span
            "route": f"need {CANARY}",  # allowed key, forbidden value
            "tenant_id": str(uuid4()),  # identifiers never ride on span attributes
        },
    ):
        pass
    attrs = dict(sink.spans[0].attributes or {})
    assert attrs == {"provider_code": "p_search", "outcome": "ok"}
    assert drops.counts["traces"] == 3
    with tracer.start_as_current_span("not.in.catalog", attributes={"stage": "x"}):
        pass
    assert not dict(sink.spans[1].attributes or {})  # an unknown span keeps nothing


def test_resume_never_duplicates_span_ids() -> None:
    tracer, sink, _ = traced()
    run = uuid4()
    for _ in range(2):  # the first attempt, then the resume after a crash
        with runspans.span(tracer, "stage", run, ("plan", 1), {"stage": "plan", "attempt": 1}):
            pass
    first, again = sink.spans
    assert first.context.span_id == again.context.span_id == span_id_for(run, "stage", "plan", 1)
    assert first.context.trace_id == trace_id_for(run) == run.int
    assert first.parent is not None and first.parent.span_id == span_id_for(run, "root")
    with runspans.span(tracer, "stage", run, ("plan", 2), {"stage": "plan", "attempt": 2}):
        pass  # a new attempt is a new span
    assert sink.spans[2].context.span_id != first.context.span_id
    runspans.emit_root(tracer, run, T0, T0 + timedelta(minutes=9), {"state": "COMPLETED"})
    root = sink.spans[3]
    assert root.parent is None and root.context.trace_id == run.int
    assert root.context.span_id == span_id_for(run, "root") == first.parent.span_id
    assert root.end_time is not None and root.start_time is not None
    assert (root.end_time - root.start_time) == 9 * 60 * 10**9
    with tracer.start_as_current_span("stage"):
        pass  # planned ids are consumed once; the next span is random again
    assert sink.spans[4].context.span_id != root.context.span_id


def test_instruments_drop_undeclared_labels() -> None:
    reader = InMemoryMetricReader()
    drops = DropCounter()
    inst = Instruments(CAT, MeterProvider(metric_readers=[reader]).get_meter("t"), drops)
    inst.record(
        "api_requests_total",
        1,
        {"route_class": "v1_runs", "code_class": "2xx", "tenant_id": "t-1", "path": "/v1/runs/abc"},
    )
    inst.record("api_requests_total", 1, {"route_class": "made_up", "code_class": "2xx"})
    with pytest.raises(KeyError):
        inst.record("not_in_catalog", 1)  # a defect, caught by the contract check
    data = reader.get_metrics_data()
    assert data is not None
    points = [
        p
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
        if m.name == "api_requests_total"
        for p in m.data.data_points
    ]
    labels = sorted(tuple(sorted(dict(p.attributes or {}).items())) for p in points)
    assert labels == [(("code_class", "2xx"),), (("code_class", "2xx"), ("route_class", "v1_runs"))]
    assert drops.counts["metrics"] == 3


def test_log_lines_carry_only_allowed_fields() -> None:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    drops = DropCounter()
    handler.setFormatter(JsonFormatter(CAT, drops, "worker"))
    log = logging.getLogger("obs-test")
    log.addHandler(handler)
    log.propagate = False
    log.setLevel(logging.INFO)
    try:
        log.warning(
            "provider_failed",
            extra={
                "provider_code": "p_search",
                "attempt": 2,
                "prompt": f"summarise {CANARY}",
                "route": "https://x.example/?token=abc",
            },
        )
        try:
            raise ValueError(f"page text {CANARY}")
        except ValueError:
            log.exception("stage_failed", extra={"stage": "plan"})
        log.info("Something with spaces and %s", "args")  # free text never reaches the line
    finally:
        log.removeHandler(handler)
    lines = [json.loads(x) for x in buf.getvalue().splitlines()]
    assert CANARY not in buf.getvalue() and "x.example" not in buf.getvalue()
    assert lines[0] == {
        "event": "provider_failed",
        "level": "WARNING",
        "severity": "WARNING",  # Cloud Logging reads severity from structured lines
        "service": "worker",
        "provider_code": "p_search",
        "attempt": 2,
        "logger": "obs-test",
    }
    assert lines[1]["error_class"] == "ValueError" and len(lines[1]["message_hash"]) == 16
    assert lines[2]["event"] == "log"
    assert drops.counts["logs"] == 2
