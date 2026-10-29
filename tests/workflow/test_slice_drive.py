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

    started, release = asyncio.Event(), asyncio.Event()

    async def deps_factory(*_: Any) -> Any:
        return _deps()

    async def slice_body(*_: Any) -> runner.SliceOutcome:
        started.set()
        await release.wait()
        return runner.SliceOutcome("done", "COMPLETED")

    _lifecycle(monkeypatch, used=0)  # the second copy lands before the first began its slice
    monkeypatch.setattr(runner, "_slice", slice_body)
    tenant, run = uuid4(), uuid4()
    first = asyncio.create_task(runner.execute_slice(deps_factory, None, None, tenant, run, 0))  # type: ignore[arg-type]
    await asyncio.wait_for(started.wait(), 5)
    second = await runner.execute_slice(deps_factory, None, None, tenant, run, 0)  # type: ignore[arg-type]
    assert second == runner.SliceOutcome("busy", "RUNNING")
    release.set()
    assert (await asyncio.wait_for(first, 5)).outcome == "done"
    assert run not in runner._IN_FLIGHT  # released: the next slice of this run may run here


def _deps(**extra: Any) -> Any:
    """Run deps enough for the runner's own code: a transaction that yields nothing."""
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime

    @asynccontextmanager
    async def txn(_tenant: Any) -> Any:
        yield None

    class Clock:
        def now(self) -> datetime:
            return datetime.now(UTC)

    class Spec:
        version = "test"

    fields = {
        "txn": staticmethod(txn),
        "tenant_id": None,
        "run_id": None,
        "connectors": None,
        "clock": Clock(),
        "spec": Spec(),
        **extra,
    }
    return type("D", (), fields)()


def _lifecycle(monkeypatch: Any, *, used: int, status: RunStatus = RunStatus.RUNNING) -> None:
    """The run row as the runner reads it: its status and how many slices it began."""
    from account_intelligence.runs import lifecycle

    async def current_status(_s: Any, _run: Any) -> RunStatus:
        return status

    async def slices_used(_s: Any, _run: Any) -> int:
        return used

    monkeypatch.setattr(lifecycle, "current_status", current_status)
    monkeypatch.setattr(lifecycle, "slices_used", slices_used)


@pytest.mark.asyncio
async def test_a_stale_redelivery_is_a_noop_even_while_the_run_is_busy(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Cloud Tasks gave up on slice 0's long first attempt and redelivered it every five minutes
    for 20 hours while the run's later slices held the lease (staging 2026-10-26: 240 attempts,
    all 503). A copy of a slice the run has moved past has nothing to run: it is a noop (200,
    dropped), whether this process or another worker holds the run."""
    from uuid import uuid4

    started, release = asyncio.Event(), asyncio.Event()

    async def deps_factory(*_: Any) -> Any:
        return _deps()

    async def slice_body(*_: Any) -> runner.SliceOutcome:
        started.set()
        await release.wait()
        return runner.SliceOutcome("awaiting", "AWAITING_APPROVAL")

    _lifecycle(monkeypatch, used=4)  # slices 0 to 3 began; slice 3 is in flight here
    monkeypatch.setattr(runner, "_slice", slice_body)
    tenant, run = uuid4(), uuid4()
    first = asyncio.create_task(runner.execute_slice(deps_factory, None, None, tenant, run, 3))  # type: ignore[arg-type]
    await asyncio.wait_for(started.wait(), 5)
    stale = await runner.execute_slice(deps_factory, None, None, tenant, run, 0)  # type: ignore[arg-type]
    assert stale == runner.SliceOutcome("noop", "RUNNING")
    same = await runner.execute_slice(deps_factory, None, None, tenant, run, 3)  # type: ignore[arg-type]
    assert same == runner.SliceOutcome("noop", "RUNNING")  # this slice began: nothing left
    ahead = await runner.execute_slice(deps_factory, None, None, tenant, run, 4)  # type: ignore[arg-type]
    assert ahead == runner.SliceOutcome("busy", "RUNNING")  # not begun: redelivered later
    release.set()
    assert (await asyncio.wait_for(first, 5)).outcome == "awaiting"


class _Snap:
    """A checkpoint snapshot: where the graph stands."""

    def __init__(self, checkpoint_id: str, pending: tuple[str, ...]) -> None:
        self.config = {"configurable": {"checkpoint_id": checkpoint_id}}
        self.next = pending
        self.tasks: tuple[Any, ...] = ()
        self.values = {"started": True}


class _StuckGraph:
    """A graph whose pending node never finishes inside a slice; ``marks`` is what each
    ``aget_state`` answers, in order (the last one repeats)."""

    def __init__(self, *marks: _Snap) -> None:
        self.marks = list(marks)

    async def aget_state(self, _config: Any) -> _Snap:
        return self.marks.pop(0) if len(self.marks) > 1 else self.marks[0]

    async def astream(self, *_: Any, **__: Any) -> Any:
        await asyncio.sleep(3600)
        yield {}


class _Factory:
    def __init__(self, graph: Any) -> None:
        self.graph = graph

    async def build(self, _deps: Any, _saver: Any) -> Any:
        return self.graph

    async def initial(self, _deps: Any) -> dict[str, Any]:
        return {}

    def target(self, _values: Any) -> Any:
        raise AssertionError("a stuck graph never ends through target()")


def _checkpointer() -> Any:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def saver(_tenant: Any) -> Any:
        yield None

    return saver


async def _run_slice(
    monkeypatch: Any,
    graph: Any,
    counts: list[int],
    *,
    limit: int = 3,
    replies: tuple[int, ...] = (0, 0),
) -> Any:
    """One slice of ``graph`` with a 10 ms timer; ``counts`` is the no-progress counter as the
    run row would hold it (one element), updated the way ``lifecycle.count_no_progress`` does."""
    from account_intelligence.runs import lifecycle

    ended: list[tuple[str, int]] = []

    async def begin(*_: Any) -> None:
        return None

    async def status(_: Any) -> RunStatus:
        return RunStatus.RUNNING

    async def count_no_progress(_s: Any, _run: Any, *, progressed: bool) -> int:
        counts[0] = 0 if progressed else counts[0] + 1
        return counts[0]

    async def end_no_progress(_deps: Any, stage: str, stalled: int, _settings: Any) -> Any:
        ended.append((stage, stalled))
        return runner.SliceOutcome("done", "PARTIAL")

    monkeypatch.setattr(runner, "_begin", begin)
    monkeypatch.setattr(runner, "_status", status)
    monkeypatch.setattr(runner, "CANCEL_POLL_S", 0.001)
    seen = iter(replies)

    async def model_replies(_s: Any, _run: Any) -> int:
        return next(seen)

    monkeypatch.setattr(lifecycle, "model_replies", model_replies)
    monkeypatch.setattr(lifecycle, "count_no_progress", count_no_progress)
    monkeypatch.setattr(runner, "_end_no_progress", end_no_progress)
    settings = runner.SliceSettings(slice_seconds=0.01, max_no_progress_slices=limit)
    outcome = await runner._slice(
        _deps(),
        _checkpointer(),
        None,
        None,
        None,
        7,
        settings,
        None,
        _Factory(graph),  # type: ignore[arg-type]
    )
    return outcome, ended


@pytest.mark.asyncio
async def test_slices_that_move_nothing_end_the_run_past_the_limit(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Three slices in a row that time out with the checkpoint where they found it end the run
    with the pending node named; the slices before the limit continue as before."""
    counts = [0]
    stuck = _StuckGraph(_Snap("c9", ("interpret",)))
    for expected in (1, 2):
        outcome, ended = await _run_slice(monkeypatch, stuck, counts)
        assert outcome.outcome == "continue" and counts == [expected] and ended == []
    outcome, ended = await _run_slice(monkeypatch, stuck, counts)
    assert outcome == runner.SliceOutcome("done", "PARTIAL")
    assert ended == [("interpret", 3)]


@pytest.mark.asyncio
async def test_a_slice_that_moves_the_checkpoint_starts_the_count_again(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A slice that times out after the graph wrote a new checkpoint is progress: the count of
    empty slices starts again at zero, however many came before it."""
    counts = [2]
    moving = _StuckGraph(_Snap("c9", ("interpret",)), _Snap("c10", ("pipelines",)))
    outcome, ended = await _run_slice(monkeypatch, moving, counts)
    assert outcome.outcome == "continue" and counts == [0] and ended == []


@pytest.mark.asyncio
async def test_a_long_node_that_stores_new_model_replies_is_progress(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The checkpoint stays on the pending node but the slice stored new model replies (the next
    slice reuses them): the count of empty slices starts again, not grows."""
    counts = [2]
    stuck = _StuckGraph(_Snap("c9", ("pipelines",)))
    outcome, ended = await _run_slice(monkeypatch, stuck, counts, replies=(10, 25))
    assert outcome.outcome == "continue" and counts == [0] and ended == []
    outcome, ended = await _run_slice(monkeypatch, stuck, counts, replies=(25, 25))
    assert counts == [1]


@pytest.mark.asyncio
async def test_the_no_progress_limit_is_never_off(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A limit of zero (a misconfiguration) still ends the run at the first empty slice: the
    caps may be off, a run that does nothing may not run forever."""
    counts = [0]
    outcome, ended = await _run_slice(
        monkeypatch, _StuckGraph(_Snap("c1", ("distill",))), counts, limit=0
    )
    assert outcome.outcome == "done" and ended == [("distill", 1)]


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

    async def slices_used(_s: Any, _run: Any) -> int:
        return 5  # slices 0 to 4 began: slice 3 is well past

    monkeypatch.setattr(lifecycle, "current_status", current_status)
    monkeypatch.setattr(lifecycle, "begin_slice", begin_slice)
    monkeypatch.setattr(lifecycle, "slices_used", slices_used)

    @asynccontextmanager
    async def txn(_tenant: Any) -> Any:
        yield None

    deps = type("D", (), {"txn": staticmethod(txn), "tenant_id": None, "run_id": None})()
    outcome = await runner._begin(deps, runner.SliceSettings(), 3)  # type: ignore[arg-type]
    assert outcome is not None and outcome.outcome == "noop"
    assert begun == [3]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "resumes"),
    [
        (RunStatus.RUNNING, True),
        (RunStatus.DISTILLING, True),
        (RunStatus.VERIFYING, True),
        (RunStatus.AWAITING_APPROVAL, False),
    ],
)
async def test_a_redelivered_slice_whose_first_copy_died_resumes_the_run(
    monkeypatch, status: RunStatus, resumes: bool
) -> None:  # type: ignore[no-untyped-def]
    """The worker was shut down mid-slice (liveness failures, 2026-10-28). Cloud Tasks redelivers
    the slice; it finds itself already counted but no later slice begun and the run still
    active: it resumes from the checkpoint instead of orphaning the run until the sweep. A run
    waiting for approval is left waiting."""
    from contextlib import asynccontextmanager

    from account_intelligence.runs import lifecycle

    async def current_status(_s: Any, _run: Any) -> RunStatus:
        return status

    async def begin_slice(_s: Any, _run: Any, *, expected: int | None = None) -> int | None:
        return None

    async def slices_used(_s: Any, _run: Any) -> int:
        return 4  # slices 0 to 3 began; slice 3 is the last and this is its second copy

    monkeypatch.setattr(lifecycle, "current_status", current_status)
    monkeypatch.setattr(lifecycle, "begin_slice", begin_slice)
    monkeypatch.setattr(lifecycle, "slices_used", slices_used)

    @asynccontextmanager
    async def txn(_tenant: Any) -> Any:
        yield None

    deps = type("D", (), {"txn": staticmethod(txn), "tenant_id": None, "run_id": None})()
    outcome = await runner._begin(deps, runner.SliceSettings(), 3)  # type: ignore[arg-type]
    if resumes:
        assert outcome is None  # the graph runs
    else:
        assert outcome is not None and outcome.outcome == "awaiting"
