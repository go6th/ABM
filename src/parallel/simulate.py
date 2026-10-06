"""Virtual-clock simulation of parallel execution (D-44). A SIMULATION, not a measurement.

It drives the real pure scheduler (``next_batch``), the real dependency plan (``fine_plan`` with
dynamic expansion from scripted stored rows) and the real governor decision rule
(``InMemoryGovernor``) through discrete events on a virtual clock:

* each run's units launch when ``next_batch`` picks them and the governor grants their provider a
  slot; a unit holds its slot for its scripted latency, then finishes;
* a generating unit (``people_merge``, ``people_levels``) finishing expands its dynamic set from
  the scripted stored rows, exactly as a real run reads them from its tables;
* each stub provider has its own true concurrency limit; a grant beyond it is counted as a
  violation attributable to our scheduling (the harness fails on any);
* the report gives makespan, the critical-path bound, utilisation and queue waits per provider.

With unlimited capacity the makespan equals the critical-path bound; it degrades predictably as
limits tighten. The report always says it is a simulation and never claims the 4 hour target is
met (that needs real runs, VALIDATION.md).

GUARANTEE(test_simulated_makespan_equals_the_critical_path_when_unconstrained): property.
GUARANTEE(test_simulation_has_no_limit_violation_with_50_runs): stubs count zero violations.
"""

from __future__ import annotations

import heapq
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from parallel.config import ParallelConfig, ProviderSettings
from parallel.critical_path import longest_path
from parallel.governor import InMemoryGovernor, LimitState, Shares
from parallel.models import (
    Capacity,
    Durations,
    EventKind,
    FundingMode,
    UnitEvent,
    UnitStatus,
    WorkUnit,
    derive_states,
)
from parallel.scheduler import Deadline, next_batch, remaining_path
from parallel.units import EXPAND, fine_plan

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
GENERATED_BY = {
    "people_merge": ("senior_people", "top_people"),
    "people_levels": ("priority_a_people",),
}


class VirtualClock:
    """A clock the simulation advances; ``sleep`` is never awaited in the simulation."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> datetime:
        """Virtual wall time."""
        return EPOCH + timedelta(seconds=self.t)

    def monotonic(self) -> float:
        """Virtual seconds since the start."""
        return self.t

    async def sleep(self, seconds: float) -> None:  # pragma: no cover - unused in the simulation
        """Advance virtual time."""
        self.t += seconds


@dataclass(frozen=True)
class Scenario:
    """Scripted inputs of one simulation (all synthetic)."""

    runs: int
    latency_s: Mapping[str, float]  # per unit type
    stored: Mapping[str, Sequence[str]]  # dynamic sets each run's tables would hold
    provider_limits: Mapping[str, int]  # the stub providers' TRUE concurrency limits
    governor_ceilings: Mapping[str, int]  # what our config allows (<= true limits to be safe)
    max_in_flight: int = 8
    deadline_s: float = 14400.0
    tail_reserve_s: float = 0.0


@dataclass
class ProviderStats:
    """Per provider: busy slot-seconds, grants, the highest concurrency, violations, waits."""

    busy_s: float = 0.0
    grants: int = 0
    peak: int = 0
    violations: int = 0
    queue_wait_s: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class SimReport:
    """What the simulation measured (virtual seconds)."""

    makespans_s: tuple[float, ...]
    critical_path_s: float
    utilisation: Mapping[str, float]
    providers: Mapping[str, ProviderStats]
    violations: int
    unfinished: int
    blocked: Mapping[str, int]  # blocked units by reason (deadline, dependency_failed)

    def markdown(self) -> str:
        """A deterministic report that states what it is."""
        lines = [
            "# Parallel execution simulation report",
            "",
            "**This is a simulation with stub providers and scripted latencies. It does not",
            "measure real providers and does not show that the 4 hour target is met.**",
            "",
            f"- runs: {len(self.makespans_s)}",
            f"- critical-path bound (one run, unlimited capacity): {self.critical_path_s:.1f} s",
            f"- makespan per run: min {min(self.makespans_s, default=0):.1f} s, "
            f"max {max(self.makespans_s, default=0):.1f} s",
            f"- provider limit violations: {self.violations}",
            f"- unfinished units: {self.unfinished}",
            "- blocked units: "
            + (", ".join(f"{k} {v}" for k, v in sorted(self.blocked.items())) or "none"),
            "",
            "| provider route | grants | peak in flight | utilisation | max queue wait s |",
            "| --- | --- | --- | --- | --- |",
        ]
        for route in sorted(self.providers):
            st = self.providers[route]
            lines.append(
                f"| {route} | {st.grants} | {st.peak} | {self.utilisation[route]:.2f} | "
                f"{max(st.queue_wait_s, default=0.0):.1f} |"
            )
        return "\n".join(lines) + "\n"


@dataclass
class _Run:
    idx: int
    units: dict[str, WorkUnit]
    events: list[UnitEvent] = field(default_factory=list)
    in_flight: set[str] = field(default_factory=set)
    queued_at: dict[str, float] = field(default_factory=dict)
    expanded: set[str] = field(default_factory=set)
    end_s: float = 0.0


def critical_path_bound(cfg: ParallelConfig, scenario: Scenario) -> float:
    """The longest dependency chain of one fully expanded run under the scripted latencies."""
    plan = fine_plan(cfg, stored=scenario.stored)
    deps = {u.key: u.depends_on for u in plan.units}
    durations = {u.key: scenario.latency_s.get(u.unit_type, 0.0) for u in plan.units}
    return longest_path(deps, durations)[0]


def simulate(cfg: ParallelConfig, scenario: Scenario) -> SimReport:
    """Run the scenario to completion on the virtual clock."""
    clock = VirtualClock()
    ceilings = dict(scenario.governor_ceilings)
    settings = ProviderSettings(
        floor=1,
        default_ceiling=max(ceilings.values(), default=1),
        ceilings=ceilings,
        aimd_window_s=cfg.providers.aimd_window_s,
        lease_ttl_s=1e9,  # holders never die in the simulation
        heartbeat_s=1.0,
        lease_wait_s=0.0,
        lease_poll_s=1.0,
        lease_jitter_s=0.0,
        waiter_ttl_s=1e9,
    )
    gov = InMemoryGovernor(
        settings, Shares(cfg.run_share_pct, cfg.tenant_share_pct, cfg.aging_step_s), clock
    )
    for code, ceiling in ceilings.items():  # start at the ceiling: the simulation tests capacity
        gov.state(code).limit = LimitState(ceiling, ceiling, 1)
    durations = Durations(dict(scenario.latency_s), cfg.unmeasured_p95_s)
    deadline = Deadline(EPOCH + timedelta(seconds=scenario.deadline_s), scenario.tail_reserve_s)
    empty: dict[str, Sequence[str]] = {}
    runs = [
        _Run(i, {u.key: u for u in fine_plan(cfg, stored=empty).units})
        for i in range(scenario.runs)
    ]
    paths = [remaining_path(r.units, durations) for r in runs]
    dirty = set(range(len(runs)))
    refused: set[int] = set()  # runs with a unit the governor refused: retry after a release
    stats: dict[str, ProviderStats] = {}
    active: dict[str, int] = {}
    heap: list[tuple[float, int, int, str, str | None, str | None]] = []
    seq = 0
    # LOOP(simulation): cap=sum of units over runs times 4 plus 1000 steps; exit=all runs drained
    for _ in range(sum(len(r.units) for r in runs) * 4 + 1000):
        cascade: set[int] = set()  # runs that blocked units: their dependents may block next
        for idx in sorted(dirty):
            run = runs[idx]
            states = derive_states(run.units.values(), run.events)
            for key, state in states.items():
                if state.status is UnitStatus.PENDING and key not in run.queued_at:
                    run.queued_at[key] = clock.t
                    run.events.append(UnitEvent(key, EventKind.QUEUED, clock.now()))
            batch = next_batch(
                run.units,
                states,
                frozenset(run.in_flight),
                Capacity(scenario.max_in_flight - len(run.in_flight)),
                FundingMode.NORMAL,
                clock.now(),
                deadline,
                durations,
                paths[idx],
            )
            for key, reason in batch.blocked:
                run.events.append(UnitEvent(key, EventKind.BLOCKED, clock.now(), reason))
                cascade.add(idx)
            for key in batch.launch:
                unit = run.units[key]
                route = unit.slot_route
                granted: str | None = None
                if route is not None:
                    granted = gov.acquire_now(route, f"t{run.idx}", f"r{run.idx}", key, 0)
                    if granted is None:
                        refused.add(idx)
                        continue
                    pst = stats.setdefault(route, ProviderStats())
                    active[route] = active.get(route, 0) + 1
                    pst.grants += 1
                    pst.peak = max(pst.peak, active[route])
                    if active[route] > scenario.provider_limits.get(route, active[route]):
                        pst.violations += 1
                    pst.queue_wait_s.append(clock.t - run.queued_at[key])
                run.in_flight.add(key)
                run.events.append(UnitEvent(key, EventKind.STARTED, clock.now()))
                took = 0.0 if unit.body == EXPAND else scenario.latency_s.get(unit.unit_type, 0.0)
                seq += 1
                heapq.heappush(heap, (clock.t + took, seq, run.idx, key, route, granted))
        dirty = cascade
        if dirty:
            continue
        if not heap:
            break
        t, _, idx, key, done_route, done_lease = heapq.heappop(heap)
        clock.t = t
        run = runs[idx]
        run.in_flight.discard(key)
        run.events.append(UnitEvent(key, EventKind.FINISHED, clock.now()))
        run.end_s = max(run.end_s, t)
        dirty = {idx}
        if done_route is not None and done_lease is not None:
            active[done_route] -= 1
            stats[done_route].busy_s += scenario.latency_s.get(run.units[key].unit_type, 0.0)
            for gst in gov.providers.values():
                gst.leases.pop(done_lease, None)
            dirty |= refused  # a slot was released: refused runs may now be granted
            refused = set()
        if _expand(cfg, scenario, run, key):
            paths[idx] = remaining_path(run.units, durations)
    unfinished = sum(
        1
        for run in runs
        for s in derive_states(run.units.values(), run.events).values()
        if s.status in (UnitStatus.PENDING, UnitStatus.RUNNING)
    )
    blocked: dict[str, int] = {}
    for run in runs:
        for e in run.events:
            if e.event is EventKind.BLOCKED and e.reason:
                blocked[e.reason] = blocked.get(e.reason, 0) + 1
    makespans = tuple(r.end_s for r in runs)
    horizon = max(makespans, default=0.0) or 1.0
    utilisation = {
        route: st.busy_s / (horizon * max(1, ceilings.get(route, 1))) for route, st in stats.items()
    }
    return SimReport(
        makespans,
        critical_path_bound(cfg, scenario),
        utilisation,
        stats,
        sum(st.violations for st in stats.values()),
        unfinished,
        blocked,
    )


def _expand(cfg: ParallelConfig, scenario: Scenario, run: _Run, finished_key: str) -> bool:
    """When a generator finishes, rebuild the plan with the stored sets it produced (True when the
    plan changed)."""
    unit = run.units.get(finished_key)
    if unit is None or unit.unit_type not in GENERATED_BY:
        return False
    run.expanded.update(GENERATED_BY[unit.unit_type])
    stored = {k: v for k, v in scenario.stored.items() if k in run.expanded}
    plan = fine_plan(cfg, stored=stored)
    new_units = {u.key: u for u in plan.units}
    # a replaced placeholder leaves the plan: its expansion now carries the dependency
    run.units = new_units
    return True
