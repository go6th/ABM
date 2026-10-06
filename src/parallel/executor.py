"""Bounded async execution of units (the scheduler's shell; P9, P11, P12, P13).

One ``run_phase`` call executes as many units as it can and returns a merged state update:

1. the ready set is re-derived from the **stored unit events** every iteration (never from memory),
   so a resumed slice, a second worker or a killed worker all see the same truth; a unit that has
   a ``started`` event but is not in this executor's in-flight set is an orphan of a dead worker
   and runs again (its finished paid calls replay from the ledger);
2. ``next_batch`` (pure) picks the launches; each launch is an ``asyncio`` task with the unit's
   priority and the cooperative cancellation check in context variables the connector reads;
3. a unit's **input** is the phase's base state plus the merged results of its transitive
   dependencies only, so its input never depends on sibling completion order (P1);
4. outcomes are typed and recorded as insert-only events with timings and lease waits (P13):
   ``finished`` (result stored with the event), ``cap_denied`` (partial result stored; the unit
   runs again after an approval, M3), ``requeued`` (no provider slot in the bounded wait; age
   kept), ``cancelled`` (token fired; runs again on resume), ``failed`` (retryable: handed to the
   node RetryPolicy, never retried here), ``failed_terminal`` (out of attempts or a defect),
   ``blocked`` (dependency failed, deadline, budget; its rows are recorded by ``hooks.on_unrun``).
   A failed or poisoned unit never cancels siblings (P11);
5. no database transaction is held across a body: every event write is its own short transaction
   through the store (P12); at most ``max_in_flight_units`` bodies run at once.

Exit reasons (4.2): ``converged``, ``deadline``, ``budget``, ``paused``, ``cancelled``,
``retryable`` (the first retryable error, by unit key, is re-raised by the caller for the node
RetryPolicy) and ``no_progress`` (``SchedulerDeadlock``: the remainder is blocked
``dependency_failed``; the loop never hangs).

GUARANTEE(test_executor_resume_rederives_the_ready_set_from_events): P9.
GUARANTEE(test_a_failing_unit_never_cancels_its_siblings): P11.
GUARANTEE(test_executor_outputs_are_identical_under_random_completion_orders): P1.
GUARANTEE(test_executor_raises_scheduler_deadlock_and_blocks_the_remainder): 4.2 no_progress.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from account_intelligence.connectors.errors import RetryableProviderError
from account_intelligence.connectors.governance import CANCEL_CHECK, UNIT_PRIORITY, SlotUnavailable
from parallel.cancel import CancelToken
from parallel.errors import Cancelled, SchedulerDeadlock
from parallel.merge import Reducer, merge_in_key_order
from parallel.models import (
    Capacity,
    Durations,
    EventKind,
    ExitReason,
    FundingMode,
    UnitEvent,
    UnitStatus,
    WorkUnit,
    derive_states,
    fold,
)
from parallel.scheduler import Deadline, next_batch, remaining_path


@dataclass(frozen=True)
class StoredEvent:
    """A unit event with the small result map stored beside it (finished, cap_denied, blocked)
    and its position in the run's insert-only log (``seq``, strictly increasing)."""

    event: UnitEvent
    result: Mapping[str, Any] | None = None
    seq: int = 0


class EventStore(Protocol):
    """Insert-only unit definitions and events (``work_units``, ``work_unit_events``)."""

    async def define(self, units: Sequence[WorkUnit]) -> None:
        """Record unit definitions once (idempotent)."""
        ...

    async def load(self, after: int = 0) -> list[StoredEvent]:
        """The run's events with ``seq > after``, in insertion order."""
        ...

    async def append(self, event: UnitEvent, result: Mapping[str, Any] | None = None) -> None:
        """Insert one event (its own short transaction)."""
        ...


@dataclass(frozen=True)
class BodyOutcome:
    """What a body returned: its result map and whether the cap stopped it part way (M3)."""

    result: Mapping[str, Any]
    cap_denied: bool = False


Body = Callable[[WorkUnit, Mapping[str, Any]], Awaitable[BodyOutcome]]


class PhaseHooks(Protocol):
    """What the workflow and the credit manager provide to the executor."""

    async def funding(self) -> FundingMode:
        """The current funding mode (live spend against soft and hard limits, pause state)."""
        ...

    async def on_cap_denied(self, unit: WorkUnit) -> None:
        """Open (idempotently) the pause request for the current cap version."""
        ...

    async def on_unrun(self, unit: WorkUnit, reason: str) -> Mapping[str, Any]:
        """Record the rows of a unit that will not run (blocked or failed) through the tracker and
        return its result map (coverage updates and completion), never a silent skip."""
        ...

    async def run_cancelled(self) -> bool:
        """Whether the run was cancelled (checked every iteration)."""
        ...


class ExecClock(Protocol):
    """Wall time for events and the deadline, a monotonic clock and a sleep."""

    def now(self) -> datetime:
        """Wall time."""
        ...

    def monotonic(self) -> float:
        """Monotonic seconds."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Wait."""
        ...


@dataclass
class PhaseResult:
    """How the phase ended and the merged update of every unit that produced a result."""

    exit: ExitReason
    update: dict[str, Any]
    error: BaseException | None = None
    blocked: dict[str, str] = field(default_factory=dict)
    unmeasured: tuple[str, ...] = ()


def _closure(units: Mapping[str, WorkUnit], key: str) -> set[str]:
    """Transitive dependencies of ``key`` (keys inside ``units`` only)."""
    seen: set[str] = set()
    stack = list(units[key].depends_on)
    # LOOP(dependency closure): cap=number of units (each visited once); exit=all reached
    while stack:
        d = stack.pop()
        if d in seen or d not in units:
            continue
        seen.add(d)
        stack.extend(units[d].depends_on)
    return seen


class Executor:
    """Runs one phase of units (see the module docstring)."""

    def __init__(
        self,
        store: EventStore,
        bodies: Mapping[str, Body],
        hooks: PhaseHooks,
        clock: ExecClock,
        reducers: Mapping[str, Reducer],
        *,
        max_in_flight: int,
        unit_attempts: int,
        stall_wait_s: float,
        slice_stop_at: float | None = None,
    ) -> None:
        if max_in_flight < 1:
            raise ValueError("max_in_flight must be at least 1")
        self.store = store
        self.bodies = bodies
        self.hooks = hooks
        self.clock = clock
        self.reducers = reducers
        self.max_in_flight = max_in_flight
        self.unit_attempts = unit_attempts
        self.stall_wait_s = stall_wait_s
        self.slice_stop_at = slice_stop_at

    async def _event(
        self,
        unit: WorkUnit,
        kind: EventKind,
        *,
        reason: str | None = None,
        result: Mapping[str, Any] | None = None,
        lease_wait_s: float = 0.0,
        error_class: str | None = None,
        provider: str | None = None,
    ) -> None:
        await self.store.append(
            UnitEvent(
                unit.key,
                kind,
                self.clock.now(),
                reason=reason,
                provider_code=provider,
                lease_wait_s=lease_wait_s,
                error_class=error_class,
            ),
            result,
        )

    async def run_phase(
        self,
        units: Sequence[WorkUnit],
        base_state: Mapping[str, Any],
        deadline: Deadline,
        durations: Durations,
        token: CancelToken | None = None,
    ) -> PhaseResult:
        """Execute until an exit reason (see the module docstring)."""
        by_key = {u.key: u for u in units}
        self._base: Mapping[str, Any] = base_state
        token = token or CancelToken()
        await self.store.define(units)
        path = remaining_path(by_key, durations)
        tasks: dict[str, asyncio.Task[tuple[str, Any]]] = {}
        first_retryable: dict[str, BaseException] = {}
        blocked_now: dict[str, str] = {}
        unmeasured: set[str] = set()
        log: list[StoredEvent] = []
        states = derive_states(units, [])
        self._results = ResultIndex()

        async def refresh() -> None:
            """Read only the events appended since the last read and fold them in (P9: the
            events stay the truth; a second worker's events are read the same way)."""
            new = await self.store.load(after=log[-1].seq if log else 0)
            log.extend(new)
            fold(states, (s.event for s in new))
            self._results.add(new)

        await refresh()
        queued: set[str] = {s.event.unit_key for s in log if s.event.event is EventKind.QUEUED}
        exit_reason: ExitReason | None = None
        try:
            # LOOP(scheduler loop): cap=10 iterations per unit plus 100 (each launches, blocks,
            # awaits a completion or waits a bounded stall); exit=converged, deadline, budget,
            # paused, cancelled, retryable, no_progress (SchedulerDeadlock)
            for _ in range(10 * len(by_key) + 100):
                await refresh()
                if await self.hooks.run_cancelled():
                    token.cancel("run_cancelled")
                if self.slice_stop_at is not None and self.clock.monotonic() >= self.slice_stop_at:
                    token.cancel("slice_deadline")
                funding = await self.hooks.funding()
                stopping = token.is_set() or bool(first_retryable)
                for key in sorted(by_key):
                    if key not in queued and states[key].status is UnitStatus.PENDING:
                        await self._event(by_key[key], EventKind.QUEUED)
                        queued.add(key)
                if not stopping:
                    batch = next_batch(
                        by_key,
                        states,
                        frozenset(tasks),
                        Capacity(run_slots=self.max_in_flight - len(tasks)),
                        funding,
                        self.clock.now(),
                        deadline,
                        durations,
                        path,
                    )
                    unmeasured.update(batch.unmeasured)
                    for key, reason in batch.blocked:
                        result = await self.hooks.on_unrun(by_key[key], reason)
                        await self._event(
                            by_key[key], EventKind.BLOCKED, reason=reason, result=result
                        )
                        blocked_now[key] = reason
                    for key in batch.launch:
                        tasks[key] = asyncio.create_task(self._run_unit(by_key, key, log, token))
                        await self._event(by_key[key], EventKind.STARTED)
                    if batch.blocked or batch.launch:
                        continue
                    if batch.done:
                        exit_reason = ExitReason.CONVERGED
                        break
                    if not tasks:
                        if batch.stall == "deadlock":
                            pending = tuple(
                                k
                                for k in sorted(by_key)
                                if states[k].status in (UnitStatus.PENDING, UnitStatus.RUNNING)
                            )
                            for key in pending:
                                result = await self.hooks.on_unrun(by_key[key], "dependency_failed")
                                await self._event(
                                    by_key[key],
                                    EventKind.BLOCKED,
                                    reason="dependency_failed",
                                    result=result,
                                )
                            raise SchedulerDeadlock(pending)
                        if batch.stall == "funding" and funding is FundingMode.PAUSED:
                            exit_reason = ExitReason.PAUSED
                            break
                        await self.clock.sleep(self.stall_wait_s)  # capacity or held: bounded wait
                        continue
                elif not tasks:
                    exit_reason = (
                        ExitReason.RETRYABLE
                        if first_retryable
                        else ExitReason.CANCELLED
                        if token.reason == "run_cancelled"
                        else ExitReason.PAUSED
                        if token.reason == "paused"
                        else ExitReason.DEADLINE
                    )
                    break
                done, _ = await asyncio.wait(
                    set(tasks.values()), return_when=asyncio.FIRST_COMPLETED
                )
                for finished in done:
                    key, outcome = finished.result()
                    del tasks[key]
                    await self._settle(by_key[key], states[key].failures, outcome, first_retryable)
                    if isinstance(outcome, BodyOutcome) and outcome.cap_denied:
                        await self.hooks.on_cap_denied(by_key[key])
            else:  # pragma: no cover - the cap is a backstop; every iteration makes progress
                raise SchedulerDeadlock(tuple(sorted(tasks)))
        except BaseException:
            # a slice timeout or a deadlock: never leave an orphaned body running
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            raise
        await refresh()
        stored = log
        update = self._merge(units, stored)
        if blocked_now or any(
            s.event.event in (EventKind.BLOCKED, EventKind.FAILED_TERMINAL) for s in stored
        ):
            update.setdefault("references", {})
            update["references"] = {
                **update["references"],
                "parallel_incomplete": "units_blocked_or_failed",
            }
        error = first_retryable[min(first_retryable)] if first_retryable else None
        reasons = {s.event.reason for s in stored if s.event.event is EventKind.BLOCKED}
        if exit_reason is ExitReason.CONVERGED and "budget_cap" in reasons:
            exit_reason = ExitReason.BUDGET
        elif exit_reason is ExitReason.CONVERGED and "deadline" in reasons:
            exit_reason = ExitReason.DEADLINE
        assert exit_reason is not None
        return PhaseResult(exit_reason, update, error, blocked_now, tuple(sorted(unmeasured)))

    async def _run_unit(
        self,
        by_key: Mapping[str, WorkUnit],
        key: str,
        stored: Sequence[StoredEvent],
        token: CancelToken,
    ) -> tuple[str, Any]:
        """Run one body under the unit's context variables; return the outcome or the error."""
        unit = by_key[key]
        inputs = self._inputs(by_key, key, stored)
        UNIT_PRIORITY.set(unit.priority)
        CANCEL_CHECK.set(token.raise_if_set)
        try:
            return key, await self.bodies[unit.body](unit, inputs)
        except Exception as exc:  # typed below in _settle; never lost
            return key, exc

    def _inputs(
        self, by_key: Mapping[str, WorkUnit], key: str, stored: Sequence[StoredEvent]
    ) -> dict[str, Any]:
        """Base state plus the merged results of the unit's transitive dependencies (P1)."""
        deps = _closure(by_key, key)
        results = self._results.final
        return merge_in_key_order(
            {k: v for k, v in results.items() if k in deps}, self.reducers, self._base
        )

    async def _settle(
        self,
        unit: WorkUnit,
        failures: int,
        outcome: Any,
        first_retryable: dict[str, BaseException],
    ) -> None:
        """Record the typed outcome of one finished task."""
        if isinstance(outcome, BodyOutcome):
            kind = EventKind.CAP_DENIED if outcome.cap_denied else EventKind.FINISHED
            await self._event(unit, kind, result=dict(outcome.result))
        elif isinstance(outcome, SlotUnavailable):
            await self._event(
                unit,
                EventKind.REQUEUED,
                reason="no_provider_slot",
                lease_wait_s=outcome.waited_s,
                provider=outcome.provider_code,
            )
        elif isinstance(outcome, Cancelled):
            await self._event(unit, EventKind.CANCELLED, reason=outcome.reason)
        elif isinstance(outcome, RetryableProviderError) and failures + 1 < self.unit_attempts:
            await self._event(
                unit, EventKind.FAILED, reason="retryable", error_class=type(outcome).__name__
            )
            first_retryable[unit.key] = outcome
        else:
            reason = "retries_exhausted" if isinstance(outcome, RetryableProviderError) else "error"
            result = await self.hooks.on_unrun(unit, "source_failed")
            await self._event(
                unit,
                EventKind.FAILED_TERMINAL,
                reason=reason,
                result=result,
                error_class=type(outcome).__name__,
            )

    def _merge(self, units: Sequence[WorkUnit], stored: Sequence[StoredEvent]) -> dict[str, Any]:
        """Merge the final result of every unit, in unit-key order (D-42)."""
        keys = {u.key for u in units}
        results = {k: v for k, v in final_results(stored).items() if k in keys}
        return merge_in_key_order(results, self.reducers)


class ResultIndex:
    """Per unit: the result of its terminal event (finished, failed_terminal, blocked); for a unit
    blocked ``budget_cap`` after a cap denial, the partial result of that denial is kept too, so
    a declined pause ends with partial results (D-45). Built incrementally as events are read;
    equal to folding the whole log at once (``final_results``)."""

    def __init__(self) -> None:
        self.final: dict[str, Mapping[str, Any]] = {}
        self._partial: dict[str, Mapping[str, Any]] = {}

    def add(self, stored: Iterable[StoredEvent]) -> None:
        """Fold more events in, in log order."""
        for s in stored:
            key = s.event.unit_key
            if key in self.final:
                continue  # terminal already: later duplicates change nothing
            if s.event.event is EventKind.CAP_DENIED and s.result is not None:
                self._partial[key] = s.result
            elif s.event.event in (
                EventKind.FINISHED,
                EventKind.FAILED_TERMINAL,
                EventKind.BLOCKED,
            ):
                result = dict(s.result or {})
                if s.event.event is EventKind.BLOCKED and key in self._partial:
                    partial = self._partial[key]
                    result = {**partial, **{k: v for k, v in result.items() if k not in partial}}
                self.final[key] = result


def final_results(stored: Sequence[StoredEvent]) -> dict[str, Mapping[str, Any]]:
    """The ``ResultIndex`` of a whole log at once."""
    index = ResultIndex()
    index.add(stored)
    return index.final
