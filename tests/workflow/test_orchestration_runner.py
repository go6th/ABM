"""The slice runner's orchestration decisions (review 2026-10-30): F4 the slice ceiling keeps what
the run found, F5 a dead worker's slice is resumed, F7 queueing and finished calls are not
stalling, F10 a lapsed wait is not a yes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from account_intelligence.connectors.errors import LeaseUnavailable
from account_intelligence.connectors.governance import SlotUnavailable
from account_intelligence.workflow import runner
from account_intelligence.workflow.transitions import RunStatus
from tests.workflow.test_slice_drive import (
    _checkpointer,
    _deps,
    _lifecycle,
    _run_slice,
    _Snap,
    _StuckGraph,
)

# F7 ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_calls_finished_in_a_slice_are_progress(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The checkpoint did not move and no model reply was stored, but the slice finished provider
    calls (a pull over a slow provider): the count of empty slices starts again."""
    counts = [2]
    stuck = _StuckGraph(_Snap("c9", ("pull",)))
    outcome, ended = await _run_slice(monkeypatch, stuck, counts, calls=(10, 14))
    assert outcome.outcome == "continue" and counts == [0] and ended == []
    outcome, ended = await _run_slice(monkeypatch, stuck, counts, calls=(14, 14))
    assert counts == [1]  # nothing at all: counted


class _QueuedGraph:
    """A graph whose node waits for a provider slot the governor never grants in its bound."""

    async def aget_state(self, _config: Any) -> _Snap:
        return _Snap("c9", ("pull",))

    async def astream(self, *_: Any, **__: Any) -> Any:
        raise SlotUnavailable("p_search", 30.0)
        yield {}


@pytest.mark.asyncio
async def test_a_slice_that_only_waited_for_a_slot_never_counts_toward_the_limit(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    counts = [0]
    for _ in range(6):  # twice the limit: queueing is not stalling
        outcome, ended = await _run_slice(monkeypatch, _QueuedGraph(), counts)
        assert outcome.outcome == "continue" and ended == []
        assert outcome.delay_s == runner.SliceSettings().slot_retry_delay_s
    assert counts == [0]


@pytest.mark.asyncio
async def test_three_slices_that_truly_do_nothing_still_end_the_run(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    counts = [0]
    stuck = _StuckGraph(_Snap("c9", ("interpret",)))
    for _ in range(2):
        outcome, _ = await _run_slice(monkeypatch, stuck, counts)
        assert outcome.outcome == "continue" and outcome.delay_s == 0.0
    outcome, ended = await _run_slice(monkeypatch, stuck, counts)
    assert outcome.outcome == "done" and ended == [("interpret", 3)]


# F4 ---------------------------------------------------------------------------------------------


def _ceiling_deps(monkeypatch: Any, *, used: int, waits: int, usable: bool) -> list[dict[str, Any]]:
    """Fake the run row for the slice-start check; returns the ``end_from_active`` calls."""
    from account_intelligence.runs import lifecycle, stop

    ended: list[dict[str, Any]] = []

    async def current_status(_s: Any, _run: Any) -> RunStatus:
        return RunStatus.RUNNING

    async def begin_slice(_s: Any, _run: Any, *, expected: int | None = None) -> int:
        return used

    async def pause_slices(_s: Any, _run: Any) -> int:
        return waits

    async def has_usable_output(_s: Any, _run: Any) -> bool:
        return usable

    async def end_from_active(
        _s: Any, _tracker: Any, _run: Any, target: RunStatus, **kw: Any
    ) -> str:
        ended.append({"target": target, **kw})
        return target.value

    monkeypatch.setattr(lifecycle, "current_status", current_status)
    monkeypatch.setattr(lifecycle, "begin_slice", begin_slice)
    monkeypatch.setattr(lifecycle, "pause_slices", pause_slices)
    monkeypatch.setattr(lifecycle, "end_from_active", end_from_active)
    monkeypatch.setattr(stop, "has_usable_output", has_usable_output)
    monkeypatch.setattr(runner, "_emit_root", lambda *a, **k: None)
    return ended


class _CreatedSession:
    async def execute(self, *_: Any, **__: Any) -> Any:
        return SimpleNamespace(scalar_one=lambda: None)


def _ceiling_run_deps() -> Any:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def txn(_tenant: Any) -> Any:
        yield _CreatedSession()

    deps = _deps(txn=staticmethod(txn))
    deps.tracker = lambda s: None  # type: ignore[attr-defined]
    return deps


@pytest.mark.asyncio
async def test_the_slice_ceiling_keeps_what_the_run_found(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A run past max_slices that holds facts and files ends PARTIAL and is charged for its real
    spend; it used to end FAILED with a full refund."""
    ended = _ceiling_deps(monkeypatch, used=9, waits=0, usable=True)
    outcome = await runner._begin(_ceiling_run_deps(), runner.SliceSettings(max_slices=8), 8)
    assert outcome == runner.SliceOutcome("done", "PARTIAL")
    assert ended[0]["target"] is RunStatus.PARTIAL and ended[0]["reason"] == "slice_ceiling"
    assert ended[0]["refund_all"] is False


@pytest.mark.asyncio
async def test_the_slice_ceiling_still_refunds_a_run_that_holds_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ended = _ceiling_deps(monkeypatch, used=9, waits=0, usable=False)
    outcome = await runner._begin(_ceiling_run_deps(), runner.SliceSettings(max_slices=8), 8)
    assert outcome == runner.SliceOutcome("done", "FAILED")
    assert ended[0]["refund_all"] is True and ended[0]["reason"] == "slice_ceiling"


@pytest.mark.asyncio
async def test_slices_spent_waiting_for_a_person_do_not_count_toward_the_ceiling(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    ended = _ceiling_deps(monkeypatch, used=11, waits=3, usable=True)  # 8 research slices
    assert await runner._begin(_ceiling_run_deps(), runner.SliceSettings(max_slices=8), 10) is None
    assert ended == []
    ended = _ceiling_deps(monkeypatch, used=12, waits=3, usable=True)  # a ninth
    outcome = await runner._begin(_ceiling_run_deps(), runner.SliceSettings(max_slices=8), 11)
    assert outcome is not None and outcome.status == "PARTIAL"


# F5 ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "used", "answer"),
    [
        (RunStatus.RUNNING, 4, "busy"),  # the last slice begun: its worker may have died
        (RunStatus.RUNNING, 6, "noop"),  # the run moved past this slice: a stale copy
        (RunStatus.AWAITING_APPROVAL, 4, "noop"),  # waiting for a person: nothing to resume
        (RunStatus.COMPLETED, 4, "noop"),
    ],
)
async def test_a_redelivery_that_meets_another_workers_live_lease_waits_for_it_to_lapse(
    monkeypatch, status: RunStatus, used: int, answer: str
) -> None:  # type: ignore[no-untyped-def]
    """The dead worker's lease is still live when Cloud Tasks retries its slice: the retry must
    not be dropped (it used to answer noop), or the run waits for the orphan sweep."""
    _lifecycle(monkeypatch, used=used, status=status)
    out = await runner._yield(_deps(), 3, other_worker=True)
    assert out.outcome == answer


@pytest.mark.asyncio
async def test_a_second_copy_inside_this_process_is_still_a_noop(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _lifecycle(monkeypatch, used=4)
    assert (await runner._yield(_deps(), 3)).outcome == "noop"


class _Ledger:
    def __init__(self, answers: list[bool]) -> None:
        self.answers = answers
        self.renewals = 0

    async def acquire_lease(self, _ctx: Any, _owner: str) -> int:
        self.renewals += 1
        if not self.answers.pop(0):
            raise LeaseUnavailable()
        return 1


def _leased_deps(ledger: _Ledger) -> Any:
    deps = _deps()
    deps.connectors = SimpleNamespace(
        ledger=ledger,
        worker_id="w1",
        config=SimpleNamespace(
            defaults=SimpleNamespace(ledger=SimpleNamespace(lease_heartbeat_s=0.01))
        ),
    )
    deps.call_context = lambda: None
    return deps


class _SlowGraph:
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
async def test_the_lease_is_renewed_while_a_node_runs_and_a_lost_lease_stops_the_slice(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    async def status(_: Any) -> RunStatus:
        return RunStatus.RUNNING

    monkeypatch.setattr(runner, "_status", status)
    monkeypatch.setattr(runner, "CANCEL_POLL_S", 0.005)
    ledger = _Ledger([True, True, False])  # two renewals, then another worker holds it
    graph = _SlowGraph()
    with pytest.raises(runner.LeaseLost):
        await asyncio.wait_for(runner._drive(graph, None, {}, _leased_deps(ledger)), 5)
    assert (
        ledger.renewals == 3 and graph.cancelled
    )  # the node stopped: two workers never drive a run


@pytest.mark.asyncio
async def test_a_slice_that_lost_its_lease_ends_without_ending_the_run(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def begin(*_: Any) -> None:
        return None

    async def status(_: Any) -> RunStatus:
        return RunStatus.RUNNING

    async def lost(*_: Any, **__: Any) -> bool:
        raise runner.LeaseLost("run")

    stopped: list[Any] = []

    async def stop_unexpected(*a: Any) -> None:
        stopped.append(a)

    class Factory:
        async def build(self, *_: Any) -> Any:
            return _StuckGraph(_Snap("c1", ("pull",)))

        async def initial(self, *_: Any) -> dict[str, Any]:
            return {}

    monkeypatch.setattr(runner, "_begin", begin)
    monkeypatch.setattr(runner, "_status", status)
    monkeypatch.setattr(runner, "_drive", lost)
    monkeypatch.setattr(runner, "_stop_unexpected", stop_unexpected)
    from account_intelligence.runs import lifecycle

    async def zero(*_: Any) -> int:
        return 0

    monkeypatch.setattr(lifecycle, "model_replies", zero)
    monkeypatch.setattr(lifecycle, "completed_calls", zero)
    out = await runner._slice(
        _deps(),
        _checkpointer(),
        None,
        None,
        None,
        3,
        runner.SliceSettings(),
        None,
        Factory(),  # type: ignore[arg-type]
    )
    assert out.outcome == "noop" and stopped == []


# F10 --------------------------------------------------------------------------------------------


class _Interrupt:
    def __init__(self, value: Any) -> None:
        self.value = value


def _paused(*values: Any) -> Any:
    return SimpleNamespace(tasks=[SimpleNamespace(interrupts=[_Interrupt(v) for v in values])])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interrupt", "lapsed", "declined", "decision"),
    [
        ({"awaiting": "cap", "reason": "tokens"}, False, False, True),  # the person said continue
        ({"awaiting": "cap", "reason": "tokens"}, True, False, False),  # nobody answered: stop
        ({"awaiting": "cap", "reason": "spend"}, False, True, False),  # declined
        ({"awaiting": "cap", "reason": "sense_pulls"}, False, True, False),
        ({"awaiting": "cap", "reason": "spend"}, False, False, True),
        ({"awaiting": "cap", "reason": "tokens"}, False, True, True),  # a declined old spend pause
        ({"awaiting": "approval", "reason": "cap"}, False, False, True),
    ],
)
async def test_a_resumed_interrupt_is_told_no_when_the_wait_lapsed_or_was_declined(
    monkeypatch, interrupt: dict[str, str], lapsed: bool, declined: bool, decision: bool
) -> None:  # type: ignore[no-untyped-def]
    from account_intelligence.runs import lifecycle

    async def marked(_s: Any, _run: Any, _slice: int) -> bool:
        return lapsed

    async def pause_declined(_s: Any, _run: Any) -> bool:
        return declined

    monkeypatch.setattr(lifecycle, "pause_lapsed", marked)
    monkeypatch.setattr(lifecycle, "pause_declined", pause_declined)
    assert await runner._resume_decision(_deps(), _paused(interrupt), 3) is decision


@pytest.mark.asyncio
async def test_the_graph_is_resumed_with_the_decision_not_always_with_yes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Wired end to end through ``_slice``: a token-guard pause whose wait lapsed resumes the
    graph with ``False`` (it used to be ``True``: the spend guard raised itself)."""
    from account_intelligence.runs import lifecycle

    seen: list[Any] = []

    class Paused(_Snap):
        def __init__(self) -> None:
            super().__init__("c9", ("token_gate",))
            self.tasks = (
                SimpleNamespace(interrupts=[_Interrupt({"awaiting": "cap", "reason": "tokens"})]),
            )

    class Graph:
        async def aget_state(self, _config: Any) -> Any:
            return Paused()

        async def astream(self, graph_input: Any, *_: Any, **__: Any) -> Any:
            seen.append(graph_input)
            raise SlotUnavailable("p_search", 1.0)
            yield {}

    class Factory:
        async def build(self, *_: Any) -> Any:
            return Graph()

        async def initial(self, *_: Any) -> dict[str, Any]:
            return {}

    async def begin(*_: Any) -> None:
        return None

    async def status(_: Any) -> RunStatus:
        return RunStatus.RUNNING

    async def zero(*_: Any) -> int:
        return 0

    async def lapsed(*_: Any) -> bool:
        return True  # (session, run, slice)

    monkeypatch.setattr(runner, "_begin", begin)
    monkeypatch.setattr(runner, "_status", status)
    monkeypatch.setattr(lifecycle, "model_replies", zero)
    monkeypatch.setattr(lifecycle, "completed_calls", zero)
    monkeypatch.setattr(lifecycle, "pause_lapsed", lapsed)
    out = await runner._slice(
        _deps(),
        _checkpointer(),
        None,
        None,
        None,
        3,
        runner.SliceSettings(),
        None,
        Factory(),  # type: ignore[arg-type]
    )
    assert out.outcome == "continue"
    assert seen[0].resume is False


@pytest.mark.asyncio
async def test_a_redelivered_slice_that_finds_an_unanswered_interrupt_records_the_wait(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """The first copy stopped the graph at a pause and died before it recorded the wait: the run
    is still RUNNING with the interrupt pending. The redelivery must not read that as an
    approval; it records the wait (the run goes to AWAITING_APPROVAL) and runs nothing."""
    ended: list[Any] = []
    drove: list[Any] = []

    class Paused(_Snap):
        def __init__(self) -> None:
            super().__init__("c9", ("token_gate",))
            self.tasks = (
                SimpleNamespace(interrupts=[_Interrupt({"awaiting": "cap", "reason": "tokens"})]),
            )

    class Graph:
        async def aget_state(self, _config: Any) -> Any:
            return Paused()

        async def astream(self, *a: Any, **k: Any) -> Any:
            drove.append(a)
            yield {}

    class Factory:
        async def build(self, *_: Any) -> Any:
            return Graph()

        async def initial(self, *_: Any) -> dict[str, Any]:
            return {}

    async def begin(_deps: Any, _settings: Any, _slice: int, began: Any = None) -> None:
        began["resumed"] = True  # slice.resumed: the slice had already begun
        return None

    async def end(*a: Any) -> runner.SliceOutcome:
        ended.append(a)
        return runner.SliceOutcome("awaiting", "AWAITING_APPROVAL")

    monkeypatch.setattr(runner, "_begin", begin)
    monkeypatch.setattr(runner, "_end", end)
    out = await runner._slice(
        _deps(),
        _checkpointer(),
        None,
        None,
        None,
        3,
        runner.SliceSettings(),
        None,
        Factory(),  # type: ignore[arg-type]
    )
    assert out.outcome == "awaiting" and len(ended) == 1 and drove == []


@pytest.mark.asyncio
async def test_a_slice_stops_when_something_else_ended_its_run(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The sweeper ended a run in the same second a redelivered slice resumed it (staging
    2026-10-30): the slice kept paying for a finished run for 23 minutes. It now stops."""

    async def status(_: Any) -> RunStatus:
        return RunStatus.PARTIAL

    monkeypatch.setattr(runner, "_status", status)
    monkeypatch.setattr(runner, "CANCEL_POLL_S", 0.005)
    graph = _SlowGraph()
    with pytest.raises(runner.LeaseLost):
        await asyncio.wait_for(runner._drive(graph, None, {}, _leased_deps(_Ledger([True]))), 5)
    assert graph.cancelled


@pytest.mark.asyncio
async def test_a_canceled_run_still_returns_canceled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def status(_: Any) -> RunStatus:
        return RunStatus.CANCELED

    monkeypatch.setattr(runner, "_status", status)
    monkeypatch.setattr(runner, "CANCEL_POLL_S", 0.005)
    assert (
        await asyncio.wait_for(
            runner._drive(_SlowGraph(), None, {}, _leased_deps(_Ledger([True]))), 5
        )
        is True
    )
