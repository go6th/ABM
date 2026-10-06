"""The pure scheduler (4.2, P2, P4, P5, P7, P10): property tests over generated DAGs and states,
plus the lying scenarios for funding, holds, deadlines and dependency failure."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from parallel.models import (
    Capacity,
    Durations,
    EventKind,
    FundingMode,
    UnitEvent,
    UnitState,
    UnitStatus,
    WorkUnit,
    derive_states,
)
from parallel.scheduler import Deadline, next_batch, remaining_path

T0 = datetime(2026, 1, 1, tzinfo=UTC)
FAR = Deadline(T0 + timedelta(hours=4), 0)
DUR = Durations({}, 60.0)
ROUTES = ["r1", "r2", "r3"]


def state(status: UnitStatus, **kw: object) -> UnitState:
    base: dict[str, object] = {
        "queued_at": None,
        "started_at": None,
        "finished_at": None,
        "failures": 0,
        "reason": None,
        "waiting": False,
    }
    base.update(kw)
    return UnitState(status, **base)  # type: ignore[arg-type]


@st.composite
def worlds(draw: st.DrawFn) -> tuple[dict[str, WorkUnit], dict[str, UnitState]]:
    n = draw(st.integers(1, 14))
    keys = [f"u{i:02d}" for i in range(n)]
    units: dict[str, WorkUnit] = {}
    states: dict[str, UnitState] = {}
    for i, key in enumerate(keys):
        deps = draw(st.lists(st.sampled_from(keys[:i]), unique=True)) if i else []
        soft = draw(st.lists(st.sampled_from(deps), unique=True)) if deps else []
        routes = tuple(draw(st.lists(st.sampled_from(ROUTES), max_size=2, unique=True)))
        units[key] = WorkUnit(
            key,
            draw(st.sampled_from(["a", "b"])),
            "body",
            depends_on=tuple(deps),
            soft_depends_on=frozenset(soft),
            routes=routes,
            priority=draw(st.integers(0, 1)),
        )
        status = draw(st.sampled_from(list(UnitStatus)))
        states[key] = state(
            status,
            queued_at=T0 + timedelta(seconds=draw(st.integers(0, 100))),
            started_at=T0 if draw(st.booleans()) else None,
        )
    return units, states


def ready(units: dict[str, WorkUnit], states: dict[str, UnitState], key: str) -> bool:
    for d in units[key].depends_on:
        s = states[d].status
        if s is UnitStatus.DONE:
            continue
        if s in (UnitStatus.FAILED, UnitStatus.BLOCKED) and d in units[key].soft_depends_on:
            continue
        return False
    return True


@given(worlds(), st.integers(0, 5), st.dictionaries(st.sampled_from(ROUTES), st.integers(0, 3)))
def test_next_batch_never_launches_before_dependencies(
    world: tuple[dict, dict], slots: int, free: dict[str, int]
) -> None:
    units, states = world
    b = next_batch(
        units, states, frozenset(), Capacity(slots, free), FundingMode.NORMAL, T0, FAR, DUR
    )
    for key in b.launch:
        assert ready(units, states, key)
        assert states[key].status in (UnitStatus.PENDING, UnitStatus.RUNNING)
    for key, reason in b.blocked:
        if reason == "dependency_failed":
            assert any(
                states[d].status in (UnitStatus.FAILED, UnitStatus.BLOCKED)
                and d not in units[key].soft_depends_on
                for d in units[key].depends_on
            )


@given(worlds(), st.integers(0, 5), st.dictionaries(st.sampled_from(ROUTES), st.integers(0, 3)))
def test_next_batch_never_exceeds_capacity(
    world: tuple[dict, dict], slots: int, free: dict[str, int]
) -> None:
    units, states = world
    b = next_batch(
        units, states, frozenset(), Capacity(slots, free), FundingMode.NORMAL, T0, FAR, DUR
    )
    assert len(b.launch) <= slots
    for route, limit in free.items():
        assert sum(1 for k in b.launch if units[k].slot_route == route) <= limit
    assert len(set(b.launch)) == len(b.launch)


@given(worlds(), st.integers(0, 5), st.sampled_from(list(FundingMode)))
def test_next_batch_is_deterministic(
    world: tuple[dict, dict], slots: int, mode: FundingMode
) -> None:
    units, states = world
    shuffled = dict(random.Random(1).sample(list(units.items()), len(units)))
    one = next_batch(units, states, frozenset(), Capacity(slots), mode, T0, FAR, DUR)
    two = next_batch(shuffled, dict(states), frozenset(), Capacity(slots), mode, T0, FAR, DUR)
    assert one == two


@given(worlds(), st.sampled_from(list(FundingMode)))
def test_funding_modes_gate_paid_launches(world: tuple[dict, dict], mode: FundingMode) -> None:
    units, states = world
    b = next_batch(units, states, frozenset(), Capacity(99), mode, T0, FAR, DUR)
    for key in b.launch:
        u = units[key]
        if mode is FundingMode.PAUSED or mode is FundingMode.STOPPED:
            assert not u.paid
        if mode is FundingMode.PRIORITY_ONLY and u.paid:
            assert u.priority > 0 or states[key].started_at is not None
    if mode is FundingMode.STOPPED:
        for key, reason in b.blocked:
            if reason == "budget_cap":
                assert units[key].paid


@settings(max_examples=150)
@given(worlds(), st.integers(1, 4), st.randoms(use_true_random=False))
def test_scheduler_terminates_or_reports_a_stall(
    world: tuple[dict, dict], slots: int, rnd: random.Random
) -> None:
    """Drive whole runs: every unit ends terminal within a bounded number of steps, units fail at
    random, and the loop never hangs (a valid DAG never reports a deadlock)."""
    units, _ = world
    events: list[UnitEvent] = []
    in_flight: set[str] = set()
    for _ in range(4 * len(units) + 5):
        states = derive_states(units.values(), events)
        b = next_batch(
            units, states, frozenset(in_flight), Capacity(slots), FundingMode.NORMAL, T0, FAR, DUR
        )
        assert b.stall != "deadlock"
        for key, reason in b.blocked:
            events.append(UnitEvent(key, EventKind.BLOCKED, T0, reason))
        for key in b.launch:
            events.append(UnitEvent(key, EventKind.STARTED, T0))
            in_flight.add(key)
        if b.done:
            break
        if not b.launch and not b.blocked and in_flight:
            key = sorted(in_flight)[0]
            in_flight.discard(key)
            kind = EventKind.FAILED_TERMINAL if rnd.random() < 0.2 else EventKind.FINISHED
            events.append(UnitEvent(key, kind, T0))
    states = derive_states(units.values(), events)
    assert all(
        s.status in (UnitStatus.DONE, UnitStatus.FAILED, UnitStatus.BLOCKED)
        for s in states.values()
    )


def _u(
    key: str, *deps: str, routes: tuple[str, ...] = ("r1",), prio: int = 0, t: str = "a"
) -> WorkUnit:
    return WorkUnit(key, t, "body", depends_on=deps, routes=routes, priority=prio)


def test_dependency_failure_blocks_unless_soft() -> None:
    units = {
        "a": _u("a"),
        "b": _u("b", "a"),
        "c": WorkUnit("c", "a", "body", depends_on=("a",), soft_depends_on=frozenset({"a"})),
    }
    states = {
        "a": state(UnitStatus.FAILED),
        "b": state(UnitStatus.PENDING),
        "c": state(UnitStatus.PENDING),
    }
    b = next_batch(units, states, frozenset(), Capacity(9), FundingMode.NORMAL, T0, FAR, DUR)
    assert b.blocked == (("b", "dependency_failed"),)
    assert b.launch == ("c",)


def test_order_is_priority_then_critical_path_then_age_then_key() -> None:
    units = {
        "x": _u("x"),
        "y": _u("y", prio=1),
        "z": _u("z"),
        "long": _u("long", t="slow"),
        "tail": _u("tail", "long", t="slow"),
    }
    states = {k: state(UnitStatus.PENDING, queued_at=T0) for k in units}
    states["z"] = state(UnitStatus.PENDING, queued_at=T0 - timedelta(seconds=5))
    dur = Durations({"slow": 100.0, "a": 1.0}, 60)
    b = next_batch(units, states, frozenset(), Capacity(9), FundingMode.NORMAL, T0, FAR, dur)
    assert b.launch == ("y", "long", "z", "x")
    assert remaining_path(units, dur)["long"] == 200.0


def test_deadline_blocks_units_that_cannot_finish_and_flags_unmeasured_durations() -> None:
    units = {"fast": _u("fast", t="fast"), "slow": _u("slow", t="slow"), "unk": _u("unk", t="?")}
    states = {k: state(UnitStatus.PENDING) for k in units}
    near = Deadline(T0 + timedelta(seconds=1000), 500)  # 500 s left before the tail reserve
    dur = Durations({"fast": 100.0, "slow": 900.0}, 600.0)
    b = next_batch(units, states, frozenset(), Capacity(9), FundingMode.NORMAL, T0, near, dur)
    assert b.launch == ("fast",)
    assert dict(b.blocked) == {"slow": "deadline", "unk": "deadline"}
    assert b.unmeasured == ("?",)


def test_held_routes_wait_and_report_the_stall() -> None:
    units = {"a": _u("a")}
    states = {"a": state(UnitStatus.PENDING)}
    b = next_batch(
        units,
        states,
        frozenset(),
        Capacity(9, held_routes=frozenset({"r1"})),
        FundingMode.NORMAL,
        T0,
        FAR,
        DUR,
    )
    assert b.launch == () and b.stall == "held" and not b.done


def test_paused_funding_stalls_paid_units_but_runs_unpaid_ones() -> None:
    units = {"paid": _u("paid"), "free": _u("free", routes=())}
    states = {k: state(UnitStatus.PENDING) for k in units}
    b = next_batch(units, states, frozenset(), Capacity(9), FundingMode.PAUSED, T0, FAR, DUR)
    assert b.launch == ("free",)
    b = next_batch(
        units,
        {**states, "free": state(UnitStatus.DONE)},
        frozenset(),
        Capacity(9),
        FundingMode.PAUSED,
        T0,
        FAR,
        DUR,
    )
    assert b.stall == "funding"


def test_capacity_and_deadlock_stalls_are_distinguished() -> None:
    units = {"a": _u("a"), "b": _u("b")}
    states = {"a": state(UnitStatus.PENDING), "b": state(UnitStatus.PENDING)}
    b = next_batch(
        units, states, frozenset(), Capacity(9, {"r1": 0}), FundingMode.NORMAL, T0, FAR, DUR
    )
    assert b.stall == "capacity"
    # an inconsistent plan whose dependency is not in the plan is a deadlock, never a hang
    units = {"a": _u("a"), "b": _u("b", "ghost")}
    states = {"a": state(UnitStatus.DONE), "b": state(UnitStatus.PENDING)}
    b = next_batch(units, states, frozenset(), Capacity(9), FundingMode.NORMAL, T0, FAR, DUR)
    assert b.stall == "deadlock" and not b.done


def test_derive_states_is_a_pure_function_of_events() -> None:
    units = [_u("a")]
    ev = [
        UnitEvent("a", EventKind.QUEUED, T0),
        UnitEvent("a", EventKind.STARTED, T0),
        UnitEvent("a", EventKind.FAILED, T0),
        UnitEvent("a", EventKind.QUEUED, T0 + timedelta(seconds=9)),
        UnitEvent("a", EventKind.CAP_DENIED, T0),
        UnitEvent("a", EventKind.STARTED, T0),
        UnitEvent("a", EventKind.FINISHED, T0, "ok"),
        UnitEvent("a", EventKind.BLOCKED, T0, "late duplicate"),
    ]
    s = derive_states(units, ev)["a"]
    assert s.status is UnitStatus.DONE and s.failures == 1 and s.queued_at == T0
    assert s.reason == "ok"
    assert derive_states(units, ev) == derive_states(units, list(ev))
