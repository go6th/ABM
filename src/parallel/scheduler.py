"""Scheduler core: ``next_batch`` (pure) decides which units launch now (section 4.2).

Inputs are values only (units, derived states, the executor's in-flight set, capacity, funding,
``now``, the deadline and measured durations); there is no clock, randomness or I/O, so the same
inputs always give the same batch. Rules, in order:

1. **Dependencies (P2).** A unit is ready when every dependency is done; a soft dependency that
   failed or was blocked also counts as settled. A unit whose hard dependency failed or was
   blocked is returned in ``blocked`` with ``dependency_failed`` (never skipped silently).
2. **Funding (P7, D-46, M3).** ``paused``: no paid unit launches; ``priority_only``: a paid unit
   launches only if it covers a High row or had already started once; ``stopped`` (pause declined
   or timed out): every pending paid unit is blocked ``budget_cap``. The connector's atomic reserve
   still decides every call; this only stops launching work that cannot be funded.
3. **Holds (M7).** A unit whose single route is held for an upload waits.
4. **Deadline (P10).** A unit not started whose p95 duration would end after ``deadline_at`` minus
   the tail reserve is blocked ``deadline``; an unmeasured p95 uses the flagged fallback.
5. **Order (P5).** Priority (High first), then the remaining critical path (longest first), then
   age (oldest first), then key.
6. **Capacity (P4, P12).** At most ``run_slots`` units; a fine unit with one route needs a free
   planned slot on it when the caller planned that route (a route absent from ``provider_free`` is
   left to the governor, whose lease is the authority at call time).

``stall`` explains an empty batch with nothing in flight: ``funding``, ``held``, ``capacity`` or
``deadlock`` (nothing can ever become ready: the shell raises ``SchedulerDeadlock``).

GUARANTEE(test_next_batch_never_launches_before_dependencies): property test.
GUARANTEE(test_next_batch_never_exceeds_capacity): property test.
GUARANTEE(test_next_batch_is_deterministic): property test.
GUARANTEE(test_scheduler_terminates_or_reports_a_stall): property test over whole runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from parallel.dag import topological
from parallel.models import (
    Capacity,
    Durations,
    FundingMode,
    UnitState,
    UnitStatus,
    WorkUnit,
)

SETTLED_FAIL = (UnitStatus.FAILED, UnitStatus.BLOCKED)


@dataclass(frozen=True)
class Batch:
    """What to launch now, what to block, and why nothing launched (if so)."""

    launch: tuple[str, ...]
    blocked: tuple[tuple[str, str], ...]  # (key, reason)
    stall: str | None  # funding, held, capacity, deadlock, or None
    unmeasured: tuple[str, ...]  # unit types whose p95 came from the flagged fallback
    done: bool  # nothing pending and nothing in flight


@dataclass(frozen=True)
class Deadline:
    """The run's deadline and the reserve kept for the sequential tail."""

    deadline_at: datetime
    tail_reserve_s: float


def remaining_path(units: Mapping[str, WorkUnit], durations: Durations) -> dict[str, float]:
    """For each unit, the longest p95 chain from it to any sink (itself included). One pass in
    reverse topological order (dependencies outside ``units`` are ignored)."""
    deps = {k: [d for d in u.depends_on if d in units] for k, u in units.items()}
    dependents: dict[str, list[str]] = {k: [] for k in units}
    for k, ds in deps.items():
        for d in ds:
            dependents[d].append(k)
    out: dict[str, float] = {}
    for k in reversed(topological(deps)):
        tail = max((out[c] for c in dependents[k]), default=0.0)
        out[k] = durations.p95(units[k].unit_type)[0] + tail
    return out


def next_batch(
    units: Mapping[str, WorkUnit],
    states: Mapping[str, UnitState],
    in_flight: frozenset[str],
    capacity: Capacity,
    funding: FundingMode,
    now: datetime,
    deadline: Deadline,
    durations: Durations,
    path: Mapping[str, float] | None = None,
) -> Batch:
    """See the module docstring."""
    path = path if path is not None else remaining_path(units, durations)
    blocked: list[tuple[str, str]] = []
    ready: list[WorkUnit] = []
    waiting_funding = waiting_held = False
    unmeasured: set[str] = set()
    pending = 0
    cutoff = deadline.deadline_at - timedelta(seconds=deadline.tail_reserve_s)
    for key in sorted(units):
        unit, state = units[key], states[key]
        if key in in_flight or state.status not in (UnitStatus.PENDING, UnitStatus.RUNNING):
            continue
        pending += 1
        # a dependency missing from the plan never settles (a deadlock is reported, never a hang)
        dep_states = [states[d].status if d in states else None for d in unit.depends_on]
        hard_failed = any(
            s in SETTLED_FAIL and d not in unit.soft_depends_on
            for d, s in zip(unit.depends_on, dep_states, strict=True)
        )
        if hard_failed:
            blocked.append((key, "dependency_failed"))
            continue
        if not all(
            s is UnitStatus.DONE or (s in SETTLED_FAIL and d in unit.soft_depends_on)
            for d, s in zip(unit.depends_on, dep_states, strict=True)
        ):
            continue
        if unit.paid and funding is FundingMode.STOPPED:
            blocked.append((key, "budget_cap"))
            continue
        if unit.paid and funding is FundingMode.PAUSED:
            waiting_funding = True
            continue
        if (
            unit.paid
            and funding is FundingMode.PRIORITY_ONLY
            and unit.priority <= 0
            and state.started_at is None
        ):
            waiting_funding = True
            continue
        if unit.slot_route is not None and unit.slot_route in capacity.held_routes:
            waiting_held = True
            continue
        p95, measured = durations.p95(unit.unit_type)
        if not measured:
            unmeasured.add(unit.unit_type)
        if now + timedelta(seconds=p95) > cutoff:
            blocked.append((key, "deadline"))
            continue
        ready.append(unit)
    ready.sort(
        key=lambda u: (
            -u.priority,
            -path.get(u.key, 0.0),
            states[u.key].queued_at or now,
            u.key,
        )
    )
    launch: list[str] = []
    free = dict(capacity.provider_free)
    slots = capacity.run_slots
    short_of_slots = False
    for unit in ready:
        if slots <= 0:
            short_of_slots = True
            break
        route = unit.slot_route
        if route is not None and route in free:
            if free[route] <= 0:
                short_of_slots = True
                continue
            free[route] -= 1
        launch.append(unit.key)
        slots -= 1
    stall: str | None = None
    newly_blocked = {k for k, _ in blocked}
    remaining = pending - len(newly_blocked)
    if not launch and not in_flight and remaining > 0:
        if short_of_slots:
            stall = "capacity"
        elif waiting_funding:
            stall = "funding"
        elif waiting_held:
            stall = "held"
        elif not newly_blocked:
            stall = "deadlock"
    return Batch(
        launch=tuple(launch),
        blocked=tuple(blocked),
        stall=stall,
        unmeasured=tuple(sorted(unmeasured)),
        done=remaining == 0 and not in_flight and not launch,
    )
