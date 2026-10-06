"""Critical path and schedule analysis from unit events (pure; P13: the schedule is recorded).

* ``longest_path(deps, duration)``: the length and node sequence of the longest dependency path,
  ties broken by name, so the result is deterministic. On a DAG this is the makespan lower bound
  with unlimited capacity.
* ``analyse(units, events)``: from the insert-only events alone, each unit's queue wait (started
  minus first queued), run time (finished minus last started), the lease waits recorded on events,
  the observed makespan, the critical path over observed run times and the idle time (makespan
  minus the critical path length, the share of wall clock not explained by dependencies).

GUARANTEE(test_critical_path_equals_the_longest_path): property-tested against brute force.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from parallel.dag import topological
from parallel.models import EventKind, UnitEvent, WorkUnit


def longest_path(
    deps: Mapping[str, Iterable[str]], duration: Mapping[str, float]
) -> tuple[float, tuple[str, ...]]:
    """Length and path of the longest chain (``DagInvalid`` on a cycle)."""
    order = topological(deps)
    best: dict[str, tuple[float, tuple[str, ...]]] = {}

    def pick(cands: Iterable[tuple[float, tuple[str, ...]]]) -> tuple[float, tuple[str, ...]]:
        # longest first; among equals the lexicographically smallest path (deterministic)
        return min(cands, key=lambda c: (-c[0], c[1]), default=(0.0, ()))

    for node in order:
        base = pick(best[d] for d in deps[node])
        best[node] = (base[0] + float(duration.get(node, 0.0)), (*base[1], node))
    return pick(best.values())


@dataclass(frozen=True)
class UnitTiming:
    """Observed timing of one unit (seconds; None when it never reached that point)."""

    key: str
    queue_wait_s: float | None
    run_s: float | None
    lease_wait_s: float
    outcome: str


@dataclass(frozen=True)
class ScheduleReport:
    """What the events say about the schedule."""

    makespan_s: float
    critical_path_s: float
    critical_path: tuple[str, ...]
    idle_s: float
    timings: tuple[UnitTiming, ...]


def analyse(units: Iterable[WorkUnit], events: Iterable[UnitEvent]) -> ScheduleReport:
    """See the module docstring."""
    unit_list = list(units)
    by_unit: dict[str, list[UnitEvent]] = {u.key: [] for u in unit_list}
    for e in events:
        if e.unit_key in by_unit:
            by_unit[e.unit_key].append(e)
    first: datetime | None = None
    last: datetime | None = None
    timings: list[UnitTiming] = []
    run_time: dict[str, float] = {}
    for u in unit_list:
        evs = by_unit[u.key]
        queued = next((e.at for e in evs if e.event is EventKind.QUEUED), None)
        started = [e.at for e in evs if e.event is EventKind.STARTED]
        ended = next(
            (e for e in evs if e.event in (EventKind.FINISHED, EventKind.FAILED_TERMINAL)), None
        )
        outcome = ended.event.value if ended else (evs[-1].event.value if evs else "none")
        run_s = (ended.at - started[-1]).total_seconds() if ended and started else None
        wait = (started[0] - queued).total_seconds() if started and queued else None
        if run_s is not None:
            run_time[u.key] = run_s
        for stamp in (queued, *(started[:1]), ended.at if ended else None):
            if stamp is None:
                continue
            first = stamp if first is None or stamp < first else first
            last = stamp if last is None or stamp > last else last
        timings.append(UnitTiming(u.key, wait, run_s, sum(e.lease_wait_s for e in evs), outcome))
    makespan = (last - first).total_seconds() if first and last else 0.0
    deps = {u.key: [d for d in u.depends_on if d in by_unit] for u in unit_list}
    length, path = longest_path(deps, run_time)
    return ScheduleReport(makespan, length, path, max(0.0, makespan - length), tuple(timings))
