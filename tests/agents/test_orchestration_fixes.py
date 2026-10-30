"""Orchestration review 2026-10-30, the pipelines node (F1 to F3).

* F1: a retryable provider error raised by a code pipeline reaches the node ``RetryPolicy``; the
  node used to catch it with every other exception, so one 429 or 5xx ended the pipeline.
* F2: a failed pipeline's rows can never read Done (the tracker ignores a reason once a row has
  calls) and a run whose pipeline failed never ends Completed.
* F3: a technology feed handed to the pipeline at the pull ceiling is reported by the pipeline.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from langgraph.types import RetryPolicy

from account_intelligence.connectors.errors import RateLimited, RetryableProviderError
from account_intelligence.workflow.transitions import RunStatus
from agents import graph
from agents.graph import build_abm_graph
from agents.production.wiring import AbmGraphFactory
from tests.agents import worlds
from tests.agents.conftest import Harness

pytestmark = pytest.mark.asyncio


def with_node_retry(h: Harness) -> None:
    """The service graph with the node RetryPolicy the worker compiles (the harness has none)."""
    policy = RetryPolicy(
        initial_interval=0.01,
        backoff_factor=1.0,
        max_interval=0.01,
        max_attempts=3,
        jitter=False,
        retry_on=RetryableProviderError,
    )
    h.service.graph = build_abm_graph(h.service.deps, policy).compile(checkpointer=h.saver)


def flaky(h: Harness, name: str) -> list[int]:
    """Make pipeline method ``name`` raise a 429 on its first call, then work."""
    calls: list[int] = []
    real = getattr(h.pipelines, name)

    async def wrapper(*args: Any, **kw: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RateLimited("429", attempt=1)
        return await real(*args, **kw)

    setattr(h.pipelines, name, wrapper)
    return calls


@pytest.mark.parametrize("method", ["run_jobs", "write_detections", "write_stack", "run_people"])
async def test_a_retryable_pipeline_error_is_retried_by_the_node_not_swallowed(
    make, method: str
) -> None:  # type: ignore[no-untyped-def]
    h = make(worlds.rich())
    h.grant_consent()
    with_node_retry(h)
    calls = flaky(h, method)
    record, state = await h.run("r")
    assert len(calls) == 2  # the node ran the pipeline again after the 429
    counters = state["counters"]
    assert not any(k.startswith("pipelines_") and k.endswith("_failed") for k in counters)
    assert state["exits"]["pipelines"] == "run"
    if method == "run_jobs":
        assert counters["jobs_claims"] == 1


async def test_a_pipeline_error_that_is_not_retryable_still_fails_only_that_pipeline(
    make,
) -> None:  # type: ignore[no-untyped-def]
    h = make(worlds.rich())
    with_node_retry(h)

    async def broken(ctx: Any, careers: Any) -> Any:
        raise RuntimeError("synthetic store error")

    h.pipelines.run_jobs = broken  # type: ignore[method-assign]
    _, state = await h.run("r")
    assert state["counters"]["pipelines_jobs_failed"] == 1 and state["exits"]["jobs"] == "failed"
    assert h.pipelines.tech_runs == 1  # the other pipelines still ran


async def test_a_failed_jobs_pipeline_leaves_failed_rows_and_a_partial_run(
    make,
) -> None:  # type: ignore[no-untyped-def]
    """Phase 1 pulled the feed (call evidence on the posting rows), then the pipeline broke: the
    rows must not read Done and the run must not end Completed with empty workbooks."""
    h = make(worlds.rich())

    async def broken(ctx: Any, careers: Any) -> Any:
        raise RuntimeError("synthetic store error")

    h.pipelines.run_jobs = broken  # type: ignore[method-assign]
    record, state = await h.run("r")
    job_keys = [k for k, row in h.cfg.plan.items() if row.pipeline == "jobs"]
    rows = h.tracker.rows_by_run[record.run_id]
    pulled = {r.row_key: r for r in h.manifests.by_run[record.run_id].rows}
    assert any(pulled[k].call_refs for k in job_keys)  # the scenario: Phase 1 left evidence
    assert all(rows[k].status in ("partial", "blocked", "not_done") for k in job_keys)
    assert any(rows[k].reason == "source_failed" for k in job_keys)
    assert state["outcome"] == "partial"
    target, reason, refund = AbmGraphFactory().target(state)
    assert (target, reason, refund) == (RunStatus.PARTIAL, "steps_waiting", False)


async def test_a_completed_graph_with_a_failed_pipeline_counter_maps_to_partial() -> None:
    state = {"outcome": "completed", "counters": {"pipelines_people_failed": 1}}
    assert AbmGraphFactory().target(state) == (RunStatus.PARTIAL, "steps_waiting", False)
    ok = {"outcome": "completed", "counters": {}}
    assert AbmGraphFactory().target(ok)[0] is RunStatus.COMPLETED


def tech_key(h: Harness) -> str:
    return next(k for k, row in h.cfg.plan.items() if row.pipeline == "tech")


async def hang_on_tech(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph, "PULL_ROW_CEILING_S", 0.05)
    real = h.provider.call
    key = tech_key(h)

    async def stuck(ctx: Any, need: str, query: str, row_key: str) -> Any:
        if row_key == key:
            await asyncio.sleep(3600)
        return await real(ctx, need, query, row_key)

    h.provider.call = stuck  # type: ignore[method-assign]


async def test_a_tech_feed_handed_to_the_pipeline_is_reported_by_the_pipeline(
    make, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    h = make(worlds.rich())
    await hang_on_tech(h, monkeypatch)
    record, state = await asyncio.wait_for(h.run("r"), 30)
    pulled = {r.row_key: r for r in h.manifests.by_run[record.run_id].rows}
    assert pulled[tech_key(h)].blocked_reason == "owned_by_pipeline"
    row = h.tracker.rows_by_run[record.run_id][tech_key(h)]
    # not "Not done: source_failed" (the reading step's report of the hand-over reason)
    assert row.status == "done" and row.reason is None
    assert state["counters"]["tech_rows"] == 1


async def test_a_tech_feed_the_pipeline_cannot_pull_either_is_a_visible_failure(
    make, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from agents.ports import PipelineOutcome

    h = make(worlds.rich())
    await hang_on_tech(h, monkeypatch)

    async def source_failed(ctx: Any) -> Any:
        return PipelineOutcome({"tech_rows": 0}, {"tech": "source_failed"})

    h.pipelines.write_detections = source_failed  # type: ignore[method-assign]
    record, state = await asyncio.wait_for(h.run("r"), 30)
    row = h.tracker.rows_by_run[record.run_id][tech_key(h)]
    assert row.status != "done" and row.reason == "source_failed"
    assert state["counters"]["pipelines_tech_failed"] == 1 and state["outcome"] == "partial"


# the real tracker on Postgres ------------------------------------------------------------------


@pytest.mark.requires_postgres
async def test_a_failed_source_downgrades_a_done_row_on_the_real_tracker() -> None:
    from account_intelligence.db.session import tenant_txn
    from account_intelligence.workflow.spec import load_spec
    from agents.graph import report_source_failed
    from agents.ports import RunContext
    from agents.production.tracker import TrackerAdapter
    from tests.tracker.world import fetch, ledger_done, make_world, txn

    w = make_world()
    async with txn(w) as t:
        await t.seed(w.run, w.tenant)
    owners = load_spec().coverage_owners
    adapter = TrackerAdapter(tenant_txn, owners)
    ctx = RunContext(w.tenant, w.run, w.domain, "Synthetic Co", w.domain, False, "cfg")
    ref = ledger_done(w, record_count=3)
    key = "company_news"
    # the tracker's own contract: a reason is ignored once the row has calls, so the plain
    # report of a failed step reads Done (this is what the pipelines node used to rely on)
    assert (await adapter.report(ctx, key, [ref], "source_failed")).status == "done"
    await report_source_failed(adapter, ctx, {key: [ref]})
    ((status, reason),) = fetch(
        "SELECT status, reason_code FROM coverage_rows WHERE run_id = %s AND row_key = %s",
        w.run,
        key,
    )
    assert (status, reason) == ("partial", "source_failed")
    # a row no step reported yet becomes Not done with the same reason
    other = "podcasts"
    await report_source_failed(adapter, ctx, {other: []})
    ((status, reason),) = fetch(
        "SELECT status, reason_code FROM coverage_rows WHERE run_id = %s AND row_key = %s",
        w.run,
        other,
    )
    assert (status, reason) == ("not_done", "source_failed")


# the adapters hand a retryable error up below the budget and state a failure above it -----------


async def _detections_deps(attempt: int, monkeypatch: pytest.MonkeyPatch) -> Any:
    from agents.production import pipelines as module

    class Session:
        async def execute(self, *a: Any, **k: Any) -> Any:
            return SimpleNamespace(scalar_one=lambda: None)

    @asynccontextmanager
    async def txn(tenant: Any) -> Any:
        yield Session()

    async def fails(*a: Any, **k: Any) -> Any:
        raise RateLimited("429", attempt=attempt)

    monkeypatch.setattr(module, "call_operation", fails)
    deps = SimpleNamespace(
        connectors=object(),
        txn=txn,
        tenant_id=uuid4(),
        run_id=uuid4(),
        domain="synthetic.example",
        call_context=lambda: None,
        retry_max_attempts=3,
    )
    return module.RunPipelines(deps, None)  # type: ignore[arg-type]


async def test_the_detections_writer_hands_a_429_up_below_the_budget(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pipes = await _detections_deps(1, monkeypatch)
    with pytest.raises(RateLimited):
        await pipes.write_detections(None)  # type: ignore[arg-type]


async def test_the_detections_writer_states_a_source_failure_after_the_budget(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pipes = await _detections_deps(3, monkeypatch)
    out = await pipes.write_detections(None)  # type: ignore[arg-type]
    assert out.exits == {"tech": "source_failed"}


async def test_an_entity_mismatch_is_a_failed_run_with_nothing_charged() -> None:
    """The run stopped at the wrong company before the research began: FAILED with a full refund
    (it used to end PARTIAL and charge at least one credit for nothing)."""
    state = {"outcome": "failed", "error_code": "entity_mismatch", "counters": {}}
    assert AbmGraphFactory().target(state) == (RunStatus.FAILED, "entity_mismatch", True)
    other = {"outcome": "failed", "error_code": "sense_check_missing", "counters": {}}
    assert AbmGraphFactory().target(other) == (RunStatus.PARTIAL, "sense_check_missing", False)


# F9: every external await has a ceiling shorter than the slice -----------------------------------


async def test_a_person_search_that_never_answers_is_a_failed_search_not_a_hung_node(
    make, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from collections.abc import Mapping

    from tests.agents.fakes import Page

    world = worlds.rich()
    d = world.domain
    world.pages["basic_fit_check"] = [
        Page(
            f"https://{d}/about",
            "fit: Northwind employs about 5,000 people in field service.\n"
            "exec: Avery Quinn, Chief Information Officer leads technology.",
        )
    ]
    h = make(world)

    def with_executive(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        out = []
        for p in payload["payloads"]:
            if "Avery Quinn, Chief Information Officer" in p["text"]:
                out.append(
                    {
                        "row_key": p["row_key"],
                        "attribute": "executive_role",
                        "value": "Avery Quinn, Chief Information Officer",
                        "origin": "keyword_match",
                        "evidence": [
                            {
                                "payload_ref": p["payload_ref"],
                                "quote": "Avery Quinn, Chief Information Officer leads technology.",
                            }
                        ],
                    }
                )
        return {"candidates": out}

    for kind in ("abm_pass1_extract", "abm_pass2_extract"):
        assert h.llm is not None
        h.llm.scripts[kind] = with_executive
    monkeypatch.setattr(graph, "PULL_ROW_CEILING_S", 0.05)
    real = h.provider.call
    hung: list[str] = []

    async def stuck(ctx: Any, need: str, query: str, row_key: str) -> Any:
        if query.startswith("Avery Quinn Northwind"):
            hung.append(row_key)
            await asyncio.sleep(3600)
        return await real(ctx, need, query, row_key)

    h.provider.call = stuck  # type: ignore[method-assign]
    _, state = await asyncio.wait_for(h.run("r"), 30)
    assert len(hung) == 3  # the three person searches were started and gave up
    assert state["counters"]["person_searches"] == 0
    assert state["counters"]["person_searches_failed"] == 3
    assert state["outcome"] in ("completed", "partial")


async def test_a_follow_up_pull_that_never_answers_is_a_named_gap(
    make, monkeypatch, caplog
) -> None:  # type: ignore[no-untyped-def]
    h = make(worlds.tangled())
    monkeypatch.setattr(graph, "PULL_ROW_CEILING_S", 0.05)
    real = h.provider.call
    assert h.llm is not None

    async def stuck(ctx: Any, need: str, query: str, row_key: str) -> Any:
        if h.llm.calls["abm_sense_check"] >= 1:  # only the follow-up pull the sense check asked for
            await asyncio.sleep(3600)
        return await real(ctx, need, query, row_key)

    h.provider.call = stuck  # type: ignore[method-assign]
    with caplog.at_level("WARNING"):
        _, state = await asyncio.wait_for(h.run("r"), 30)  # it used to hang for the slice
    assert state["counters"]["targeted_pulls"] == 1
    assert any(r.message == "targeted_pull.slow" for r in caplog.records)
    assert state["outcome"] in ("completed", "partial") and h.builder.builds


async def test_a_pipeline_page_read_that_never_answers_is_a_gap(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from account_intelligence.connectors import base
    from account_intelligence.connectors.base import Operation
    from jobs import adapters
    from jobs.ports import SourceUnavailable

    async def never(*_a: Any, **_k: Any) -> Any:
        await asyncio.sleep(3600)

    monkeypatch.setattr(adapters, "call_operation", never)
    monkeypatch.setattr(adapters, "SINGLE_CALL_CEILING_S", 0.05)
    calls = adapters._Calls(SimpleNamespace(connectors=object(), call_context=lambda: None))
    with pytest.raises(SourceUnavailable, match="source_slow"):
        await asyncio.wait_for(calls.call(Operation.READ_PAGE, object()), 5)
    # a feed paginates for many minutes by design: it is bounded by the slice, not by this
    assert Operation.JOB_OPENINGS not in base.SINGLE_REQUEST_OPERATIONS


# F14: a transient infrastructure error in the last four nodes is retried -------------------------


def _infra_error() -> Exception:
    from sqlalchemy.exc import OperationalError

    return OperationalError("SELECT 1", {}, ConnectionResetError("server closed the connection"))


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (ConnectionResetError("reset"), True),
        (TimeoutError(), True),
        (_infra_error(), True),
        (RateLimited("429"), False),  # a provider error: the provider policy's business
        (ValueError("bug"), False),
        (KeyError("deliverable not found"), False),
        (PermissionError("denied"), False),
    ],
)
async def test_which_errors_are_transient_infrastructure(error: Exception, transient: bool) -> None:
    assert graph.is_transient_infra(error) is transient


async def test_a_storage_service_that_answered_5xx_is_transient() -> None:
    ServiceUnavailable = type(
        "ServiceUnavailable", (Exception,), {"__module__": "google.api_core.x"}
    )
    NotFound = type("NotFound", (Exception,), {"__module__": "google.api_core.x"})
    assert graph.is_transient_infra(ServiceUnavailable("503"))
    assert not graph.is_transient_infra(NotFound("404"))


@pytest.mark.parametrize("node", ["build", "gate", "deliver"])
async def test_a_dropped_connection_in_build_gate_or_deliver_is_retried_not_the_end_of_the_run(
    make, node: str
) -> None:  # type: ignore[no-untyped-def]
    h = make(worlds.rich())
    with_node_retry(h)
    target = {
        "build": (h.builder, "build"),
        "gate": (h.gate, "check"),
        "deliver": (h.delivery, "release"),
    }[node]
    owner, method = target
    real = getattr(owner, method)
    calls: list[int] = []

    async def flaky_call(*args: Any, **kw: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise _infra_error()
        return await real(*args, **kw)

    setattr(owner, method, flaky_call)
    record, state = await h.run("r")
    assert len(calls) >= 2  # the node ran again
    assert state["outcome"] in ("completed", "partial") and h.delivery.released


async def test_a_defect_in_deliver_is_not_retried(make) -> None:  # type: ignore[no-untyped-def]
    h = make(worlds.rich())
    with_node_retry(h)
    calls: list[int] = []

    async def broken(*args: Any, **kw: Any) -> Any:
        calls.append(1)
        raise ValueError("a bug, not a blip")

    h.delivery.release = broken  # type: ignore[method-assign]
    with pytest.raises(ValueError):
        await h.run("r")
    assert len(calls) == 1
