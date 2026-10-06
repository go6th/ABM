"""Value types of parallel execution: units, unit events, the derived unit state, provider limits.

All frozen. A unit's identity is its deterministic ``key`` (D-42); the durable truth about a unit is
its insert-only event history, from which ``derive_states`` computes the current state (P9: resume
re-derives the ready set from unit events, never from memory).

GUARANTEE(test_derive_states_is_a_pure_function_of_events): the same events give the same states.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class EventKind(StrEnum):
    """Insert-only unit event kinds (``work_unit_events.event``)."""

    QUEUED = "queued"
    STARTED = "started"
    FINISHED = "finished"
    FAILED = "failed"  # retryable failure handed to the node RetryPolicy (not terminal)
    FAILED_TERMINAL = "failed_terminal"  # failed and out of attempts, or a non-retryable failure
    BLOCKED = "blocked"  # never ran: dependency_failed, deadline, credits_exhausted, budget
    CANCELLED = "cancelled"  # interrupted by the cooperative token; runs again on resume
    REQUEUED = "requeued"  # no provider slot in the bounded wait; age preserved
    CAP_DENIED = "cap_denied"  # the cap refused a paid call; runs again after an approval
    HELD = "held"  # its provider is held for an upload (exhaustion ladder); runs again later


TERMINAL_EVENTS = frozenset({EventKind.FINISHED, EventKind.FAILED_TERMINAL, EventKind.BLOCKED})


class UnitStatus(StrEnum):
    """Derived state of one unit."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"  # terminal failure
    BLOCKED = "blocked"


class FundingMode(StrEnum):
    """What the credit manager allows to launch (D-46, M1, M3)."""

    NORMAL = "normal"
    PRIORITY_ONLY = "priority_only"  # soft limit crossed: High units and units already started
    PAUSED = "paused"  # a pause request is open: no new paid launch
    STOPPED = "stopped"  # the pause was declined or timed out: paid units end Blocked budget_cap


class ExitReason(StrEnum):
    """Why the scheduler loop stopped (4.2)."""

    CONVERGED = "converged"
    DEADLINE = "deadline"
    BUDGET = "budget"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    NO_PROGRESS = "no_progress"
    RETRYABLE = "retryable"  # a unit failed retryably: hand back to the node RetryPolicy


@dataclass(frozen=True)
class WorkUnit:
    """One unit of work ``(stage, lane, source type, shard)`` with its deterministic key."""

    key: str
    unit_type: str
    body: str
    lane: str | None = None
    shard: str | None = None
    depends_on: tuple[str, ...] = ()
    soft_depends_on: frozenset[str] = frozenset()
    routes: tuple[str, ...] = ()  # routing needs the unit calls; empty for unpaid units
    priority: int = 0  # 1 when the unit covers a High tracker row
    est_cost_micros: int | None = None  # None: unknown (never zero)
    covers: tuple[str, ...] = ()  # fine unit types a composite (stage) unit implements

    @property
    def paid(self) -> bool:
        """Whether the unit makes provider calls (and so needs funding and slots)."""
        return bool(self.routes)

    @property
    def slot_route(self) -> str | None:
        """The one route the scheduler plans a slot for: only a fine unit with a single route.
        A composite unit calls several providers over time; the governor's per-call lease is then
        the only (and authoritative) limit."""
        return self.routes[0] if len(self.routes) == 1 else None


@dataclass(frozen=True)
class UnitEvent:
    """One insert-only event of a unit."""

    unit_key: str
    event: EventKind
    at: datetime
    reason: str | None = None
    provider_code: str | None = None
    lease_wait_s: float = 0.0
    error_class: str | None = None


@dataclass(frozen=True)
class UnitState:
    """What the events say about one unit now."""

    status: UnitStatus
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    failures: int  # retryable failures so far (the node RetryPolicy's attempts at this unit)
    reason: str | None
    waiting: bool  # cap_denied or held: runs again only when funding or the hold allows


def derive_states(units: Iterable[WorkUnit], events: Iterable[UnitEvent]) -> dict[str, UnitState]:
    """Fold each unit's events (in the given order) into its state. Pure and total: a unit with no
    event is pending and unqueued."""
    by_unit: dict[str, list[UnitEvent]] = {}
    for event in events:
        by_unit.setdefault(event.unit_key, []).append(event)
    out: dict[str, UnitState] = {}
    for unit in units:
        status = UnitStatus.PENDING
        queued_at: datetime | None = None
        started_at: datetime | None = None
        finished_at: datetime | None = None
        failures = 0
        reason: str | None = None
        waiting = False
        for e in by_unit.get(unit.key, ()):
            if status in (UnitStatus.DONE, UnitStatus.FAILED, UnitStatus.BLOCKED):
                break  # terminal: later events (duplicate deliveries) change nothing
            if e.event is EventKind.QUEUED:
                queued_at = queued_at or e.at  # age is preserved across re-queues
            elif e.event is EventKind.STARTED:
                status, started_at, waiting = UnitStatus.RUNNING, e.at, False
            elif e.event is EventKind.FINISHED:
                status, finished_at, reason = UnitStatus.DONE, e.at, e.reason
            elif e.event is EventKind.FAILED_TERMINAL:
                status, finished_at, reason = UnitStatus.FAILED, e.at, e.reason
            elif e.event is EventKind.BLOCKED:
                status, finished_at, reason = UnitStatus.BLOCKED, e.at, e.reason
            elif e.event is EventKind.FAILED:
                status, failures, reason = UnitStatus.PENDING, failures + 1, e.reason
            elif e.event in (EventKind.CAP_DENIED, EventKind.HELD):
                status, waiting, reason = UnitStatus.PENDING, True, e.reason
            else:  # CANCELLED, REQUEUED: back to pending, nothing else changes
                status = UnitStatus.PENDING
        out[unit.key] = UnitState(
            status, queued_at, started_at, finished_at, failures, reason, waiting
        )
    return out


@dataclass(frozen=True)
class ProviderLimit:
    """Current adaptive concurrency limit of one provider (D-43)."""

    provider_code: str
    limit: int
    ceiling: int
    floor: int = 1

    def __post_init__(self) -> None:
        if not 1 <= self.floor <= self.limit <= self.ceiling:
            raise ValueError("a provider limit must satisfy 1 <= floor <= limit <= ceiling")


@dataclass(frozen=True)
class Capacity:
    """Free slots the scheduler may plan against (the governor's leases still decide)."""

    run_slots: int  # in-flight units this worker may still start for the run
    provider_free: Mapping[str, int] = field(default_factory=dict)  # by routing need
    held_routes: frozenset[str] = frozenset()  # routes held for an upload (exhaustion ladder)


@dataclass(frozen=True)
class Durations:
    """Measured p95 durations by unit type (seconds) and the flagged fallback."""

    p95_s: Mapping[str, float]
    unmeasured_p95_s: float

    def p95(self, unit_type: str) -> tuple[float, bool]:
        """The p95 for a unit type and whether it was measured (False: flagged fallback)."""
        if unit_type in self.p95_s:
            return self.p95_s[unit_type], True
        return self.unmeasured_p95_s, False
