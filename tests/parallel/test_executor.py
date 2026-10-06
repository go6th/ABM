"""The executor (P1, P9, P11, P12, 4.2 exits) with an in-memory event store and scripted bodies."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import pytest

from account_intelligence.connectors.errors import RateLimited
from account_intelligence.connectors.governance import CANCEL_CHECK, UNIT_PRIORITY, SlotUnavailable
from account_intelligence.workflow.state import append_unique, merge_maps, sum_counters
from parallel.config import load_parallel_config
from parallel.errors import SchedulerDeadlock
from parallel.executor import BodyOutcome, Executor
from parallel.models import Durations, ExitReason, FundingMode, WorkUnit
from parallel.scheduler import Deadline
from parallel.units import body_plan
from tests.fakes.parallel import T0, ExecClock, FakeHooks, MemoryEventStore

REDUCERS = {
    "completed_stages": append_unique,
    "references": merge_maps,
    "coverage_updates": merge_maps,
    "counters": sum_counters,
    "loop_exits": merge_maps,
}
CFG = load_parallel_config()
UNITS = body_plan(CFG).units
FAR = Deadline(T0 + timedelta(hours=4), 0)
DUR = Durations({}, 60.0)


def executor(
    store: MemoryEventStore, bodies: Mapping[str, Any], hooks: FakeHooks, **kw: Any
) -> Executor:
    return Executor(
        store,
        bodies,
        hooks,
        ExecClock(),
        REDUCERS,
        max_in_flight=kw.pop("max_in_flight", 8),
        unit_attempts=kw.pop("unit_attempts", 3),
        stall_wait_s=1.0,
        **kw,
    )


def scripted(seed: int, calls: dict[str, int] | None = None) -> dict[str, Any]:
    """Bodies that finish in a seeded random order and whose result depends on their inputs."""
    rnd = random.Random(seed)
    delays = {u.body: rnd.random() / 200 for u in UNITS}

    def make(name: str) -> Any:
        async def body(unit: WorkUnit, state: Mapping[str, Any]) -> BodyOutcome:
            if calls is not None:
                calls[name] = calls.get(name, 0) + 1
            await asyncio.sleep(delays[name])
            seen = sorted((state.get("references") or {}).keys())
            return BodyOutcome(
                {
                    "completed_stages": [name],
                    "references": {f"proposals:{name}": f"{name}<-{','.join(seen)}"},
                    "counters": {"calls": 1},
                    "coverage_updates": {f"{name}:row": {"status": "done"}},
                }
            )

        return body

    return {u.body: make(u.body) for u in UNITS}


async def run(ex: Executor, **kw: Any) -> Any:
    return await ex.run_phase(UNITS, {"references": {"base": "x"}}, FAR, DUR, **kw)


@pytest.mark.asyncio
async def test_executor_outputs_are_identical_under_random_completion_orders() -> None:
    outcomes = []
    for seed in range(50):
        result = await run(executor(MemoryEventStore(), scripted(seed), FakeHooks()))
        assert result.exit is ExitReason.CONVERGED
        outcomes.append(result.update)
    assert all(o == outcomes[0] for o in outcomes)
    refs = outcomes[0]["references"]
    # each body saw exactly its transitive dependencies, never a sibling's result
    assert refs["proposals:tech"] == "tech<-base,proposals:jobs"
    assert refs["proposals:news"] == "news<-base"
    assert refs["proposals:verification"].count("proposals:") == 7
    assert outcomes[0]["counters"] == {"calls": 8}


@pytest.mark.asyncio
async def test_a_failing_unit_never_cancels_its_siblings() -> None:
    bodies = scripted(1)

    async def poisoned(unit: WorkUnit, state: Mapping[str, Any]) -> BodyOutcome:
        raise ValueError("poisoned input")

    bodies["people"] = poisoned
    store, hooks = MemoryEventStore(), FakeHooks()
    result = await run(executor(store, bodies, hooks))
    assert store.kinds("body|people")[-1] == "failed_terminal"
    for sibling in ("body|news", "body|jobs", "body|baseline", "body|tech"):
        assert store.kinds(sibling)[-1] == "finished"
    # name searches need the people list: blocked with a reason, never skipped silently
    assert ("body|name_searches", "dependency_failed") in hooks.unrun
    # verification depends on everything softly: it still runs
    assert store.kinds("body|verification")[-1] == "finished"
    assert result.update["references"]["parallel_incomplete"] == "units_blocked_or_failed"
    assert "body|people:row" not in result.update["coverage_updates"]
    assert result.update["coverage_updates"]["row:body|people"]["reason"] == "source_failed"


@pytest.mark.asyncio
async def test_retryable_failure_goes_to_the_retry_policy_and_resume_skips_finished_units() -> None:
    calls: dict[str, int] = {}
    bodies = scripted(2, calls)
    first = {"done": False}
    inner = bodies["news"]

    async def flaky(unit: WorkUnit, state: Mapping[str, Any]) -> BodyOutcome:
        if not first["done"]:
            first["done"] = True
            raise RateLimited("429")
        result: BodyOutcome = await inner(unit, state)
        return result

    bodies["news"] = flaky
    store = MemoryEventStore()
    result = await run(executor(store, bodies, FakeHooks()))
    assert result.exit is ExitReason.RETRYABLE and isinstance(result.error, RateLimited)
    assert "verification" not in calls  # nothing launched after the failure
    before = dict(calls)
    result = await run(executor(store, bodies, FakeHooks()))  # the node RetryPolicy re-runs
    assert result.exit is ExitReason.CONVERGED
    for body, n in before.items():
        if body != "news":
            assert calls[body] == n  # finished units are never re-run (events are the truth)
    assert store.kinds("body|news").count("failed") == 1


@pytest.mark.asyncio
async def test_retryable_failures_beyond_the_unit_budget_are_terminal() -> None:
    bodies = scripted(3)

    async def always(unit: WorkUnit, state: Mapping[str, Any]) -> BodyOutcome:
        raise RateLimited("429 forever")

    bodies["news"] = always
    store, hooks = MemoryEventStore(), FakeHooks()
    for _ in range(2):
        assert (await run(executor(store, bodies, hooks))).exit is ExitReason.RETRYABLE
    result = await run(executor(store, bodies, hooks))
    assert result.exit is ExitReason.CONVERGED
    assert store.kinds("body|news")[-1] == "failed_terminal"


@pytest.mark.asyncio
async def test_executor_resume_rederives_the_ready_set_from_events() -> None:
    calls: dict[str, int] = {}
    store = MemoryEventStore()
    store.fail_after = 20  # the worker dies part way (after some units finished)
    with pytest.raises(KeyboardInterrupt):
        await run(executor(store, scripted(4, calls), FakeHooks()))
    finished = {k for k in store.units if store.kinds(k) and store.kinds(k)[-1] == "finished"}
    assert finished  # some work survived in the event table
    store.fail_after = None
    before = dict(calls)
    result = await run(executor(store, scripted(4, calls), FakeHooks()))
    assert result.exit is ExitReason.CONVERGED
    for key in finished:
        body = store.units[key].body
        assert calls[body] == before[body]  # finished before the kill: not run again


@pytest.mark.asyncio
async def test_executor_raises_scheduler_deadlock_and_blocks_the_remainder() -> None:
    orphan = WorkUnit("body|orphan", "orphan", "orphan", depends_on=("body|missing",))
    store, hooks = MemoryEventStore(), FakeHooks()
    ex = executor(store, scripted(5), hooks)
    with pytest.raises(SchedulerDeadlock) as info:
        await ex.run_phase([*UNITS, orphan], {}, FAR, DUR)
    assert info.value.pending == ("body|orphan",)
    assert ("body|orphan", "dependency_failed") in hooks.unrun


@pytest.mark.asyncio
async def test_cap_denial_pauses_then_an_approval_resumes_the_unit() -> None:
    calls: dict[str, int] = {}
    bodies = scripted(6, calls)
    inner = bodies["jobs"]
    denied = {"n": 0}

    async def capped(unit: WorkUnit, state: Mapping[str, Any]) -> BodyOutcome:
        out: BodyOutcome = await inner(unit, state)
        if denied["n"] == 0:
            denied["n"] += 1
            return BodyOutcome({**out.result, "counters": {"budget_exhausted": 1}}, cap_denied=True)
        return out

    bodies["jobs"] = capped
    store, hooks = MemoryEventStore(), FakeHooks()
    result = await run(executor(store, bodies, hooks))
    assert result.exit is ExitReason.PAUSED and hooks.cap_denied == ["body|jobs"]
    assert "tech" not in calls  # its dependency is waiting for funding
    hooks.modes = [FundingMode.NORMAL]  # approved: a new cap version
    result = await run(executor(store, bodies, hooks))
    assert result.exit is ExitReason.CONVERGED
    assert store.kinds("body|jobs").count("cap_denied") == 1 and calls["jobs"] == 2
    assert "budget_exhausted" not in result.update["counters"]


@pytest.mark.asyncio
async def test_a_declined_pause_ends_with_partial_results_and_budget_exit() -> None:
    bodies = scripted(7)
    inner = bodies["jobs"]

    async def capped(unit: WorkUnit, state: Mapping[str, Any]) -> BodyOutcome:
        out: BodyOutcome = await inner(unit, state)
        return BodyOutcome({**out.result, "counters": {"budget_exhausted": 1}}, cap_denied=True)

    bodies["jobs"] = capped
    store, hooks = MemoryEventStore(), FakeHooks()
    assert (await run(executor(store, bodies, hooks))).exit is ExitReason.PAUSED
    hooks.modes = [FundingMode.STOPPED]  # declined or timed out
    result = await run(executor(store, bodies, hooks))
    assert result.exit is ExitReason.BUDGET
    assert result.update["counters"]["budget_exhausted"] == 1  # the runner ends BUDGET_EXCEEDED
    assert result.update["references"]["proposals:jobs"].startswith("jobs<-")  # partial result kept
    assert ("body|jobs", "budget_cap") in hooks.unrun


@pytest.mark.asyncio
async def test_no_slot_requeues_the_unit_with_its_age() -> None:
    bodies = scripted(8)
    inner = bodies["baseline"]
    tries = {"n": 0}

    async def waits(unit: WorkUnit, state: Mapping[str, Any]) -> BodyOutcome:
        tries["n"] += 1
        if tries["n"] == 1:
            raise SlotUnavailable("p_search", 30.0)
        out: BodyOutcome = await inner(unit, state)
        return out

    bodies["baseline"] = waits
    store = MemoryEventStore()
    result = await run(executor(store, bodies, FakeHooks()))
    assert result.exit is ExitReason.CONVERGED
    assert store.kinds("body|baseline")[:4] == ["queued", "started", "requeued", "started"]
    requeued = next(e for e in store.events if e.event.event.value == "requeued")
    assert requeued.event.lease_wait_s == 30.0 and requeued.event.provider_code == "p_search"


@pytest.mark.asyncio
async def test_run_cancel_propagates_to_in_flight_units_cooperatively() -> None:
    hooks = FakeHooks()
    gate = asyncio.Event()
    bodies = scripted(9)

    async def long(unit: WorkUnit, state: Mapping[str, Any]) -> BodyOutcome:
        hooks.cancelled = True
        await gate.wait()
        check = CANCEL_CHECK.get()
        assert check is not None
        check()  # what call_operation does before each provider call
        raise AssertionError("unreachable")  # pragma: no cover

    for name in list(bodies):
        bodies[name] = long
    store = MemoryEventStore()
    ex = executor(store, bodies, hooks)
    task = asyncio.create_task(run(ex))
    await asyncio.sleep(0.01)
    gate.set()
    result = await task
    assert result.exit is ExitReason.CANCELLED
    assert any(k.count("cancelled") for k in (store.kinds(u.key) for u in UNITS))


@pytest.mark.asyncio
async def test_bounded_in_flight_and_priority_context() -> None:
    seen: list[int] = []
    active = {"n": 0, "peak": 0}

    async def body(unit: WorkUnit, state: Mapping[str, Any]) -> BodyOutcome:
        seen.append(UNIT_PRIORITY.get())
        active["n"] += 1
        active["peak"] = max(active["peak"], active["n"])
        await asyncio.sleep(0.005)
        active["n"] -= 1
        return BodyOutcome({"completed_stages": [unit.body]})

    units = body_plan(CFG, high=frozenset({"people"})).units
    ex = executor(MemoryEventStore(), {u.body: body for u in units}, FakeHooks(), max_in_flight=2)
    result = await ex.run_phase(units, {}, FAR, DUR)
    assert result.exit is ExitReason.CONVERGED and active["peak"] <= 2
    assert sorted(seen) == [0] * 7 + [1]


@pytest.mark.asyncio
async def test_deadline_blocks_late_units_and_reports_the_deadline_exit() -> None:
    store, hooks = MemoryEventStore(), FakeHooks()
    near = Deadline(T0 + timedelta(seconds=100), 0)
    durations = Durations({"verification": 500.0}, 10.0)
    result = await executor(store, scripted(10), hooks).run_phase(UNITS, {}, near, durations)
    assert result.exit is ExitReason.DEADLINE
    assert ("body|verification", "deadline") in hooks.unrun


def test_executor_rejects_a_zero_bound() -> None:
    with pytest.raises(ValueError):
        executor(MemoryEventStore(), {}, FakeHooks(), max_in_flight=0)
