"""The slice driver: a cancel stops a long node within seconds; a slot wait is queueing."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from account_intelligence.workflow import runner
from account_intelligence.workflow.transitions import RunStatus


class _LongNode:
    """A graph whose one node runs until it is cancelled (a pull calling providers)."""

    def __init__(self) -> None:
        self.cancelled = False

    async def astream(self, *_: Any, **__: Any) -> Any:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        yield {}


@pytest.mark.asyncio
async def test_a_cancel_stops_a_long_node_without_waiting_for_it(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    statuses = iter([RunStatus.RUNNING, RunStatus.CANCELED])

    async def status(_: Any) -> RunStatus:
        return next(statuses)

    monkeypatch.setattr(runner, "_status", status)
    monkeypatch.setattr(runner, "CANCEL_POLL_S", 0.01)
    graph = _LongNode()
    assert await asyncio.wait_for(runner._drive(graph, None, {}, None), 5) is True  # type: ignore[arg-type]
    assert graph.cancelled  # the node stopped: no further provider calls


@pytest.mark.asyncio
async def test_a_redelivered_slice_is_busy_while_the_same_process_runs_it(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Cloud Tasks redelivered a slice to the instance still running it (staging 2026-10-21);
    the second copy must yield (503, redelivered later), not run the graph twice at once."""
    from uuid import uuid4

    class Deps:
        connectors = None

    started, release = asyncio.Event(), asyncio.Event()

    async def deps_factory(*_: Any) -> Any:
        return Deps()

    async def status(_: Any) -> RunStatus:
        return RunStatus.RUNNING

    async def slice_body(*_: Any) -> runner.SliceOutcome:
        started.set()
        await release.wait()
        return runner.SliceOutcome("done", "COMPLETED")

    monkeypatch.setattr(runner, "_status", status)
    monkeypatch.setattr(runner, "_slice", slice_body)
    tenant, run = uuid4(), uuid4()
    first = asyncio.create_task(runner.execute_slice(deps_factory, None, None, tenant, run, 0))  # type: ignore[arg-type]
    await asyncio.wait_for(started.wait(), 5)
    second = await runner.execute_slice(deps_factory, None, None, tenant, run, 0)  # type: ignore[arg-type]
    assert second == runner.SliceOutcome("busy", "RUNNING")
    release.set()
    assert (await asyncio.wait_for(first, 5)).outcome == "done"
    assert run not in runner._IN_FLIGHT  # released: the next slice of this run may run here


@pytest.mark.asyncio
async def test_a_finished_graph_is_not_a_cancel(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class Done:
        async def astream(self, *_: Any, **__: Any) -> Any:
            yield {}

    async def status(_: Any) -> RunStatus:
        return RunStatus.RUNNING

    monkeypatch.setattr(runner, "_status", status)
    assert await runner._drive(Done(), None, {}, None) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_stale_redelivery_of_a_finished_slice_does_not_begin_again(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A redelivered copy of slice N landing after the first copy finished (and enqueued N+1)
    finds slices_used past N: it neither counts a slice nor runs the graph."""
    from contextlib import asynccontextmanager

    from account_intelligence.runs import lifecycle

    begun: list[int | None] = []

    async def current_status(_s: Any, _run: Any) -> RunStatus:
        return RunStatus.RUNNING

    async def begin_slice(_s: Any, _run: Any, *, expected: int | None = None) -> int | None:
        begun.append(expected)
        return None  # slices_used != expected: already begun

    monkeypatch.setattr(lifecycle, "current_status", current_status)
    monkeypatch.setattr(lifecycle, "begin_slice", begin_slice)

    @asynccontextmanager
    async def txn(_tenant: Any) -> Any:
        yield None

    deps = type("D", (), {"txn": staticmethod(txn), "tenant_id": None, "run_id": None})()
    outcome = await runner._begin(deps, runner.SliceSettings(), 3)  # type: ignore[arg-type]
    assert outcome is not None and outcome.outcome == "noop"
    assert begun == [3]
