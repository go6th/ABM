"""Runner v2: time-sliced, resumable execution of Workflow v1 (workflow spec 6 and 7).

``execute_slice`` is what the worker endpoint (``/tasks/run-slice``) calls for one slice. The
LangGraph Postgres checkpoint is the source of truth: a slice resumes from the last checkpoint,
so a killed worker loses at most the node in flight, whose paid calls replay from the ledger.

One slice:
1. a terminal run returns at once; an AWAITING_APPROVAL run waits; QUEUED moves to RUNNING; a run
   past ``max_slices`` (plus one per wait for a person: those slices are not research time) ends
   PARTIAL ``slice_ceiling`` with everything it found when it holds anything usable, FAILED with
   a full refund when it holds nothing (the rule of every other early stop);
2. the graph runs under ``asyncio.timeout(slice_seconds)``; between supersteps the run status is
   re-read and a CANCELED run stops cleanly (its settlement was made by the cancel path);
3. an approval interrupt moves the run to AWAITING_APPROVAL and arms the 24 hour timeout task
   (defect 8: the timeout cancels and refunds, it never loops into the same interrupt); a cap
   pause interrupt (parallel execution, D-45) waits the same way with ``awaiting_kind = 'cap'``,
   and its timeout records the decline and resumes the run to end BUDGET_EXCEEDED;
4. a slice that runs out of time enqueues the next slice (named task, idempotent) after it
   released the run lease. A slice that ran out of time without moving the run's checkpoint
   (the same node still pending, ``_mark``) counts on ``account_runs.no_progress_slices``;
   ``max_no_progress_slices`` of them in a row end the run PARTIAL with reason ``no_progress``
   (FAILED, full refund, when it holds nothing usable). This stays on while the research caps
   are off: on 2026-10-26 a staging research ran 43 slices of 1650 s with the same node pending
   and nothing ended it. "Moved" means a new checkpoint, a new stored model reply or a provider
   call finished in the slice (F7); a slice that ended because the provider governor had no
   slot is queueing, not stalling: it never counts toward the limit and the next one is queued
   after a short delay. A redelivered copy of a slice the run has moved past (Cloud Tasks gave
   up on the first copy's long attempt, then retried it every five minutes for 20 hours) is a
   ``noop``, not ``busy``: it has nothing to run whether or not a worker holds the run. The one
   exception is the LAST slice begun, arriving while another worker's lease is live: that may be
   the retry of a slice whose worker died, so it answers ``busy`` and is retried until the dead
   worker's lease reads stale (``_drive`` renews the lease every ``lease_heartbeat_s``; the
   ledger lets another worker take a lease its owner has not renewed for ``lease_stale_after_s``,
   so the last of Cloud Tasks' retries, about 150 s after the death, resumes the run: F5);
5. a finished graph ends the run through ``lifecycle.finish``: BUDGET_EXCEEDED when the cap was
   hit, PARTIAL with the reason when builders or the gate did not pass, else COMPLETED (which the
   tracker guard still refuses if a row is open);
6. an unexpected error ends the run PARTIAL (reason: the error class, plus a redacted detail)
   when it already holds a finished paid call, a fact or a deliverable, and FAILED with a full
   refund only when it holds nothing; it is re-raised for the logs. A cancellation or interrupt
   (SIGTERM, SIGINT) only notes itself on the run, which stays resumable from its checkpoint;
   ``runs.sweeper`` ends it ``worker_lost`` if no worker resumes it.

GUARANTEE(test_slices_that_move_nothing_end_the_run_past_the_limit)
GUARANTEE(test_a_stale_redelivery_is_a_noop_even_while_the_run_is_busy)
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol
from uuid import UUID

from langgraph.types import Command, RetryPolicy
from sqlalchemy import text

from account_intelligence.agents.prompts import prompts_hash
from account_intelligence.connectors.errors import LeaseUnavailable
from account_intelligence.connectors.governance import SlotUnavailable
from account_intelligence.platform.ports import RunScheduler
from account_intelligence.runs import lifecycle
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.engine import build_graph
from account_intelligence.workflow.handlers import build_handlers
from account_intelligence.workflow.transitions import TERMINAL, RunStatus
from obs.logging import logger

_log = logger(__name__)

DepsFactory = Callable[[UUID, UUID], Awaitable[RunDeps]]
Checkpointer = Callable[[UUID], AbstractAsyncContextManager[Any]]


class GraphFactory(Protocol):
    """An alternative graph for a run (the ABM agent layer, ``agents.production.wiring``): the
    slice lifecycle, lease, approval pauses and the one settlement stay the runner's."""

    async def build(self, deps: RunDeps, saver: Any) -> Any:
        """The compiled graph on the run's checkpointer."""
        ...

    async def initial(self, deps: RunDeps) -> dict[str, Any]:
        """The first state of the graph."""
        ...

    def target(self, values: Mapping[str, Any]) -> tuple[RunStatus, str | None, bool]:
        """``(terminal status, reason, refund_all)`` of the finished graph."""
        ...


PHASE = {
    "distill_grade": (RunStatus.RUNNING, RunStatus.DISTILLING),
    "build": (RunStatus.DISTILLING, RunStatus.VERIFYING),
}

# the runs this process is running a slice of right now (``execute_slice``)
_IN_FLIGHT: set[UUID] = set()


@dataclass(frozen=True)
class SliceOutcome:
    """What one slice did: ``continue`` (next slice queued), ``awaiting``, ``done``,
    ``canceled``, ``noop`` (nothing to do) or ``busy`` (another worker holds the run)."""

    outcome: str
    status: str
    # seconds the next slice waits before it starts (a slice that ended queueing for a provider)
    delay_s: float = 0.0


class LeaseLost(RuntimeError):  # noqa: N818 - a state, not a failure of the run
    """Another worker took this run's lease while a slice was running (this worker stalled for
    longer than the lease time). The slice stops at once and leaves the run to the new holder."""


@dataclass(frozen=True)
class SliceSettings:
    """Slice loop limits (skill: 240 s slices, at most 60) and the approval timeout."""

    # the dataclass defaults are the skill's; the container loads config/workflow_v1.yaml
    # (slice_seconds 1650, max_slices 8 on 2026-10-22) and passes a SliceSettings of its own
    slice_seconds: float = 240.0
    max_slices: int = 60
    approval_hours: float = 24.0
    # consecutive slices that ended on their timer with the checkpoint unmoved; never uncapped
    max_no_progress_slices: int = 3
    # the pause before the slice that follows one that ended queueing for a provider slot
    slot_retry_delay_s: float = 30.0


async def _status(deps: RunDeps) -> RunStatus:
    async with deps.txn(deps.tenant_id) as s:
        return await lifecycle.current_status(s, deps.run_id)


async def _finish(deps: RunDeps, target: RunStatus, reason: str | None, refund_all: bool) -> str:
    async with deps.txn(deps.tenant_id) as s:
        # lifecycle.finish settles once and stores the spend report in this transaction (K12)
        status, _ = await lifecycle.finish(
            s, deps.tracker(s), deps.run_id, target, reason=reason, refund_all=refund_all
        )
        created: Any = (
            await s.execute(
                text("SELECT created_at FROM account_runs WHERE id = :r"), {"r": deps.run_id}
            )
        ).scalar_one()
    _emit_root(deps, created, status)
    # the one readable line per run end in the cloud logs (messages are reduced to hashes there;
    # audit 2026-10-15): the state it ended in, why, and which graph ran
    _log.info(
        "run.finished",
        extra={
            "state": status,
            "stop_reason": (reason or "completed")[:120],
            "graph_version": str(deps.spec.version)[:40],
            "outcome": "refunded" if refund_all else "settled",
        },
    )
    return status


def _emit_root(deps: RunDeps, created: Any, status: str) -> None:
    """The run's root span once, after the settling transaction committed (O2); telemetry never
    changes the outcome (O1)."""
    from obs.runspans import emit_root
    from obs.setup import record, safe, tracer

    safe(
        lambda: emit_root(
            tracer(),
            deps.run_id,
            created,
            deps.clock.now(),
            {"state": status, "graph_version": str(deps.spec.version)},
        ),
        None,
    )
    record("run_transitions_total", 1, {"state": status})


async def _begin(
    deps: RunDeps,
    settings: SliceSettings,
    slice_no: int,
    began: dict[str, bool] | None = None,
) -> SliceOutcome | None:
    """Step 1. Returns an outcome when the slice should not run the graph. ``began`` is told
    ``resumed`` when this delivery is the redelivery of a slice that had already begun."""
    async with deps.txn(deps.tenant_id) as s:
        status = await lifecycle.current_status(s, deps.run_id)
        if status in TERMINAL:
            return SliceOutcome("noop", status.value)
        if status is RunStatus.AWAITING_APPROVAL:
            return SliceOutcome("awaiting", status.value)
        # LOOP(slice loop): cap=max_slices slices of slice_seconds (config/workflow_v1.yaml);
        # exit=done, awaiting, canceled, slice ceiling (PARTIAL, or FAILED when nothing is usable)
        used = await lifecycle.begin_slice(s, deps.run_id, expected=slice_no)
        if used is None:
            # this slice already began. With the lease free (the caller holds it now) there are
            # two cases. The run moved past it (a later slice began): a stale copy with nothing
            # to run, dropped (review 2026-10-22). Or this is the LAST slice that began and the
            # run is still active: its first copy died with the worker (liveness shutdowns,
            # 2026-10-28) or its answer was lost, so Cloud Tasks redelivered it. Dropping that
            # copy orphaned the run for up to the lease TTL plus the sweep; it resumes from the
            # checkpoint instead, without counting a slice again.
            begun = await lifecycle.slices_used(s, deps.run_id)
            if begun == slice_no + 1 and status in RESUMABLE:
                _log.warning("slice.resumed", extra={"count": slice_no, "stage": "redelivery"})
                if began is not None:
                    began["resumed"] = True
                return None
            return SliceOutcome("noop", status.value)
        if status is RunStatus.QUEUED:
            await lifecycle.move(s, deps.run_id, RunStatus.QUEUED, RunStatus.RUNNING)
    async with deps.txn(deps.tenant_id) as s:
        waits = await lifecycle.pause_slices(s, deps.run_id)
    if used - waits > settings.max_slices:
        return await _end_stopped(
            deps,
            "slice_ceiling",
            f"the run used {used - waits} slices of {int(settings.slice_seconds)} s"
            f" (the limit is {settings.max_slices}) and the research was not finished",
            {"slices": used},
        )
    return None


def _target(values: dict[str, Any]) -> tuple[RunStatus, str | None]:
    counters = values.get("counters") or {}
    refs = values.get("references") or {}
    if counters.get("budget_exhausted"):
        return RunStatus.BUDGET_EXCEEDED, "budget_cap"
    reason = refs.get("partial_reason") or None
    if reason:
        return RunStatus.PARTIAL, str(reason)
    return RunStatus.COMPLETED, None


async def execute_slice(
    deps_factory: DepsFactory,
    checkpointer: Checkpointer,
    scheduler: RunScheduler,
    tenant_id: UUID,
    run_id: UUID,
    slice_no: int,
    *,
    settings: SliceSettings | None = None,
    retry_policy: RetryPolicy | None = None,
    graph_factory: GraphFactory | None = None,
) -> SliceOutcome:
    """Run one slice of one run (see the module docstring). ``graph_factory`` runs another graph
    (the ABM agent layer) under the same slice lifecycle."""
    settings = settings or SliceSettings()
    # One worker per run (D-18). The run lease is the mutex across workers; it is held by the
    # process, so a redelivery that lands on the SAME process renews it and would run the slice
    # twice at once (staging 2026-10-21: Cloud Tasks gave up on a 9 minute attempt, redelivered
    # it to the same instance, and both copies raced to end the run). This set is the mutex
    # within the process.
    if run_id in _IN_FLIGHT:
        deps = await deps_factory(tenant_id, run_id)
        return await _yield(deps, slice_no)
    _IN_FLIGHT.add(run_id)
    try:
        deps = await deps_factory(tenant_id, run_id)
        owner: str | None = None
        if deps.connectors is not None:
            # the run lease is the mutex for the whole slice, not only for paid calls. A second
            # delivery of a slice (redelivery, overlap) yields; the worker endpoint answers 503
            # so Cloud Tasks redelivers it later.
            try:
                await deps.connectors.ledger.acquire_lease(
                    deps.call_context(), deps.connectors.worker_id
                )
            except LeaseUnavailable:
                return await _yield(deps, slice_no, other_worker=True)
            owner = deps.connectors.worker_id
        try:
            outcome = await _slice(
                deps,
                checkpointer,
                scheduler,
                tenant_id,
                run_id,
                slice_no,
                settings,
                retry_policy,
                graph_factory,
            )
        finally:
            if owner is not None and deps.connectors is not None:
                await deps.connectors.ledger.release_lease(deps.call_context(), owner)
    finally:
        _IN_FLIGHT.discard(run_id)
    if outcome.outcome == "continue":
        # only after the release: the next slice takes a fresh lease epoch, so a call this slice's
        # timeout cut off reads as abandoned, never as still in flight
        await scheduler.enqueue_slice(tenant_id, run_id, slice_no + 1, outcome.delay_s)
    return outcome


async def _yield(deps: RunDeps, slice_no: int, *, other_worker: bool = False) -> SliceOutcome:
    """The outcome of a slice delivery that cannot run now: ``noop`` when the run already began
    this slice or a later one (a redelivered copy; nothing is left for it, so Cloud Tasks drops
    it), else ``busy`` (503: redelivered later).

    One case of "already began" is not a copy to drop: ``other_worker`` (another process holds a
    live lease) and the run's LAST slice begun is this one and the run is still active. Its first
    copy may have died with its worker (OOM, deploy; staging 2026-10-28), in which case this
    delivery is the retry that must resume the run once the dead worker's lease reads stale. It
    answers ``busy`` (503) and Cloud Tasks delivers it again; a live holder simply makes the
    retries fail until the queue's attempts run out, which drops them (F5)."""
    async with deps.txn(deps.tenant_id) as s:
        status = await lifecycle.current_status(s, deps.run_id)
        used = await lifecycle.slices_used(s, deps.run_id)
    if used > slice_no and not (other_worker and used == slice_no + 1 and status in RESUMABLE):
        return SliceOutcome("noop", status.value)
    return SliceOutcome("busy", status.value)


def _mark(snap: Any) -> tuple[Any, ...]:
    """Where the run's checkpoint stands: the checkpoint id and the pending nodes. Two equal
    marks around a slice mean the slice moved nothing."""
    config = getattr(snap, "config", None) or {}
    configurable = config.get("configurable", {}) if isinstance(config, Mapping) else {}
    return (configurable.get("checkpoint_id"), tuple(getattr(snap, "next", None) or ()))


def _stage(snap: Any) -> str:
    """The pending node names of a snapshot, for the logs (bounded)."""
    return ",".join(str(n) for n in (getattr(snap, "next", None) or ()))[:80] or "start"


# a Stop reaches the worker within this long (one indexed status read per tick); it was 10 s, and
# a stopped run could keep paying for calls for that long (audit 2026-10-21)
CANCEL_POLL_S = 2.0
# the states a redelivered slice may resume in (QUEUED never began; AWAITING waits)
RESUMABLE = frozenset({RunStatus.RUNNING, RunStatus.DISTILLING, RunStatus.VERIFYING})


def _heartbeat_s(deps: Any) -> float | None:
    """Seconds between renewals of the run lease while a slice runs (None: no lease to renew)."""
    connectors = getattr(deps, "connectors", None)
    if connectors is None:
        return None
    try:
        return float(connectors.config.defaults.ledger.lease_heartbeat_s)
    except AttributeError:
        return None


async def _renew_lease(deps: Any) -> bool:
    """Renew this worker's run lease; False when another worker holds it now. A database error
    is not a lost lease (the next beat tries again; the lease time covers several beats)."""
    connectors = deps.connectors
    try:
        await connectors.ledger.acquire_lease(deps.call_context(), connectors.worker_id)
    except LeaseUnavailable:
        return False
    except Exception as error:  # noqa: BLE001 - a missed beat is retried; the lease outlives several
        _log.warning("slice.heartbeat_failed", extra={"error_class": type(error).__name__})
    return True


async def _canceled(deps: RunDeps) -> bool:
    """True when the user stopped the run. A run that something else ended while this slice still
    drives it (the sweeper took it for an orphan in the same second a redelivered slice resumed it,
    staging 2026-10-30: the slice then read for 23 minutes and paid for calls of a finished run)
    stops the slice with ``LeaseLost``: the run is not this worker's any more."""
    status = await _status(deps)
    if status is RunStatus.CANCELED:
        return True
    if status in TERMINAL:
        raise LeaseLost(str(deps.run_id))
    return False


async def _drive(graph: Any, graph_input: Any, config: Mapping[str, Any], deps: RunDeps) -> bool:
    """Run the graph; True when the run was canceled. The status is read after every update and
    every ``CANCEL_POLL_S`` inside a long node, so a canceled run stops calling providers within
    seconds instead of when its node ends. The run lease is renewed every ``lease_heartbeat_s``
    meanwhile: a worker that dies stops renewing, and after ``lease_stale_after_s`` another worker
    may take the run (the long ``lease_ttl_s`` is kept, so a live owner keeps its epoch). When a
    renewal finds the lease taken by another worker (this one stalled for longer than the stale
    time) the node is cancelled and ``LeaseLost`` stops the slice: two workers never drive one
    run."""

    async def stream() -> bool:
        async for _ in graph.astream(graph_input, config, stream_mode="updates"):
            if await _canceled(deps):
                return True
        return False

    task = asyncio.create_task(stream())
    beat, last_beat = _heartbeat_s(deps), time.monotonic()
    try:
        # LOOP(cancel watch): cap=the slice timeout around it; exit=graph done, canceled, timeout,
        # lease lost
        while True:
            done, _ = await asyncio.wait({task}, timeout=CANCEL_POLL_S)
            if done:
                return task.result()
            if await _canceled(deps):
                return True
            if beat is not None and time.monotonic() - last_beat >= beat:
                last_beat = time.monotonic()
                if not await _renew_lease(deps):
                    raise LeaseLost(str(deps.run_id))
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _slice(
    deps: RunDeps,
    checkpointer: Checkpointer,
    scheduler: RunScheduler,
    tenant_id: UUID,
    run_id: UUID,
    slice_no: int,
    settings: SliceSettings,
    retry_policy: RetryPolicy | None,
    graph_factory: GraphFactory | None = None,
) -> SliceOutcome:
    """The slice body, run while holding the run lease."""
    began: dict[str, bool] = {}
    early = await _begin(deps, settings, slice_no, began)
    if early is not None:
        return early

    async def on_enter(stage_id: str) -> None:
        """Move the run to DISTILLING or VERIFYING as the graph reaches those stages."""
        if stage_id in PHASE:
            expected, new = PHASE[stage_id]
            async with deps.txn(deps.tenant_id) as s:
                if await lifecycle.current_status(s, deps.run_id) is expected:
                    await lifecycle.move(s, deps.run_id, expected, new)

    config = {"configurable": {"thread_id": f"{tenant_id}:{run_id}"}, "recursion_limit": 100}
    try:
        async with checkpointer(tenant_id) as saver:
            if graph_factory is not None:
                graph = await graph_factory.build(deps, saver)
                first: dict[str, Any] = await graph_factory.initial(deps)
            else:
                graph, first = _workflow_graph(
                    deps, saver, retry_policy, on_enter, tenant_id, run_id
                )
            snap = await graph.aget_state(config)
            pending = bool(snap.next) and any(t.interrupts for t in snap.tasks)
            graph_input: Any
            if not snap.values:
                graph_input = first
            elif pending and began.get("resumed"):
                # The first copy of this slice stopped the graph at an interrupt and died before
                # it recorded the wait (the run is still RUNNING). Nobody has answered: it must
                # not read as an approval. Record the wait now, the way the first copy would.
                _log.warning("slice.wait_recorded", extra={"count": slice_no})
                return await _end(deps, scheduler, run_id, snap, settings, graph_factory)
            elif pending:
                # the run is RUNNING again: approved, or its wait lapsed or was declined. The
                # graph reads the value as the decision (F10: a lapse used to read as "yes")
                graph_input = Command(resume=await _resume_decision(deps, snap, slice_no))
            else:
                graph_input = None
            before, started = _mark(snap), deps.clock.now()
            async with deps.txn(deps.tenant_id) as s:
                replies_before = await lifecycle.model_replies(s, deps.run_id)
                calls_before = await lifecycle.completed_calls(s, deps.run_id)
            _log.info("slice.started", extra={"count": slice_no, "stage": _stage(snap)})
            try:
                async with asyncio.timeout(settings.slice_seconds):
                    if await _drive(graph, graph_input, config, deps):
                        return SliceOutcome("canceled", RunStatus.CANCELED.value)
            except (TimeoutError, SlotUnavailable) as stopped:
                # a slot the governor could not grant in its bounded wait is queueing, not a
                # failure: the slice ends like a timeout and the next one re-runs the node
                snap = await graph.aget_state(config)
                waited = (deps.clock.now() - started).total_seconds()
                return await _timed_out(
                    deps,
                    slice_no,
                    settings,
                    snap,
                    before,
                    waited,
                    replies_before,
                    calls_before,
                    queued=isinstance(stopped, SlotUnavailable),
                )
            snap = await graph.aget_state(config)
            _log.info(
                "slice.ended",
                extra={
                    "count": slice_no,
                    "stage": _stage(snap),
                    "outcome": "graph_stopped",
                    "waited_s": round((deps.clock.now() - started).total_seconds()),
                },
            )
    except LeaseLost:
        # another worker took the run while this one stalled: it is theirs now, nothing more to do
        _log.warning("slice.lease_lost", extra={"count": slice_no})
        return SliceOutcome("noop", (await _status(deps)).value)
    except Exception as error:
        if _is_interruption(error):
            # LangGraph wraps a cancelled node (NodeCancelledError): still an interruption, so
            # the run stays resumable instead of ending on a stop that was not its own
            await _note_interrupted(deps, error)
            raise
        await _stop_unexpected(deps, error)
        raise
    except BaseException as interrupted:
        # a cancelled task, SIGTERM or SIGINT: the run stays resumable from its checkpoint (the
        # lease is released by execute_slice); only the note says what happened last
        await _note_interrupted(deps, interrupted)
        raise
    return await _end(deps, scheduler, run_id, snap, settings, graph_factory)


async def _resume_decision(deps: RunDeps, snap: Any, slice_no: int) -> bool:
    """What a resumed interrupt is told: True to go on, False when the person declined or the
    wait lapsed (``expire_cap_pause`` marks the lapse; a spend pause's own record says if it was
    declined). A token-guard pause that nobody answered used to resume as "continue", which
    raised the spend guard by itself (orchestration review F10); now it stops the run with what
    it found (``token_guard_stopped``)."""
    kinds = {
        (str(v.get("awaiting")), str(v.get("reason")))
        for task in snap.tasks
        for item in task.interrupts
        for v in [item.value]
        if isinstance(v, dict)
    }
    async with deps.txn(deps.tenant_id) as s:
        if await lifecycle.pause_lapsed(s, deps.run_id, slice_no):
            return False
        if any(kind[0] == "cap" and kind[1] in ("spend", "sense_pulls") for kind in kinds):
            return not await lifecycle.pause_declined(s, deps.run_id)
    return True


async def _timed_out(
    deps: RunDeps,
    slice_no: int,
    settings: SliceSettings,
    snap: Any,
    before: tuple[Any, ...],
    waited: float,
    replies_before: int | None = None,
    calls_before: int | None = None,
    *,
    queued: bool = False,
) -> SliceOutcome:
    """A slice that ran out of time: ``continue`` while the run moves, and the end of the run
    once ``max_no_progress_slices`` slices in a row left the run where they found it. The run
    moved when its checkpoint moved, when the slice stored new model replies, or when provider
    calls finished in it: a node longer than a slice still pays for answers the next slice
    reuses, and a pull over a slow provider completes a few calls per slice (F7). A slice that
    ended because the provider governor granted no slot (``queued``) is waiting its turn behind
    other work, not stalling: it resets the count when it moved something and otherwise leaves it
    alone, and the next slice starts after ``slot_retry_delay_s``. Such slices still count
    against ``max_slices``, which ends a run that is never served."""
    progressed = _mark(snap) != before
    stage = _stage(snap)
    async with deps.txn(deps.tenant_id) as s:
        if not progressed and replies_before is not None:
            progressed = await lifecycle.model_replies(s, deps.run_id) > replies_before
        if not progressed and calls_before is not None:
            progressed = await lifecycle.completed_calls(s, deps.run_id) > calls_before
    if queued and not progressed:
        _log.info(
            "slice.ended",
            extra={
                "count": slice_no,
                "stage": stage,
                "outcome": "queued",
                "waited_s": round(waited),
            },
        )
        return SliceOutcome(
            "continue", (await _status(deps)).value, delay_s=max(0.0, settings.slot_retry_delay_s)
        )
    async with deps.txn(deps.tenant_id) as s:
        stalled = await lifecycle.count_no_progress(s, deps.run_id, progressed=progressed)
    _log.info(
        "slice.ended",
        extra={
            "count": slice_no,
            "stage": stage,
            "outcome": "progressed" if progressed else "no_progress",
            "waited_s": round(waited),
            "slices": stalled,
        },
    )
    delay = max(0.0, settings.slot_retry_delay_s) if queued else 0.0
    if stalled < max(1, settings.max_no_progress_slices):
        return SliceOutcome("continue", (await _status(deps)).value, delay_s=delay)
    return await _end_no_progress(deps, stage, stalled, settings)


async def _end_no_progress(
    deps: RunDeps, stage: str, stalled: int, settings: SliceSettings
) -> SliceOutcome:
    """End a run that made no progress (see ``_end_stopped``)."""
    return await _end_stopped(
        deps,
        "no_progress",
        f"{stage} made no progress in {stalled} slices of {int(settings.slice_seconds)} s;"
        " the node never finished inside a slice",
        {"stage": stage, "slices": stalled},
    )


async def _end_stopped(
    deps: RunDeps, reason: str, detail: str, log_extra: Mapping[str, Any]
) -> SliceOutcome:
    """End a run the platform stopped (no progress, the slice ceiling): PARTIAL with everything it
    found when it holds anything usable (credits settle by the real spend), else FAILED with a
    full refund. The same rule as an unexpected error."""
    from account_intelligence.runs.stop import has_usable_output

    async with deps.txn(deps.tenant_id) as s:
        if await lifecycle.current_status(s, deps.run_id) in TERMINAL:
            return SliceOutcome("done", (await lifecycle.current_status(s, deps.run_id)).value)
        usable = await has_usable_output(s, deps.run_id)
        status = await lifecycle.end_from_active(
            s,
            deps.tracker(s),
            deps.run_id,
            RunStatus.PARTIAL if usable else RunStatus.FAILED,
            reason=reason,
            detail=detail,
            refund_all=not usable,
        )
        created: Any = (
            await s.execute(
                text("SELECT created_at FROM account_runs WHERE id = :r"), {"r": deps.run_id}
            )
        ).scalar_one()
    _emit_root(deps, created, status)
    _log.warning(
        "run.finished",
        extra={
            **log_extra,
            "state": status,
            "stop_reason": reason,
            "graph_version": str(deps.spec.version)[:40],
            "outcome": "settled" if usable else "refunded",
        },
    )
    return SliceOutcome("done", status)


def _is_interruption(error: BaseException) -> bool:
    """Whether the error is, or wraps, a cancellation or interrupt of the process."""
    seen: set[int] = set()
    current: BaseException | None = error
    # LOOP(cause chain): cap=the chain length (each exception visited once); exit=end of chain
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, asyncio.CancelledError | KeyboardInterrupt | SystemExit):
            return True
        if type(current).__name__ == "NodeCancelledError":
            return True
        current = current.__cause__ or current.__context__
    return False


async def _stop_unexpected(deps: RunDeps, error: Exception) -> None:
    """The one terminal-state handler for an unexpected error: PARTIAL with the error class as
    the reason and a redacted detail when the run holds anything usable (a finished paid call, a
    fact, a deliverable); FAILED with a full refund only when it holds nothing."""
    from account_intelligence.runs.stop import describe, has_usable_output

    if (await _status(deps)) in TERMINAL:
        return
    detail = describe(error)
    reason = type(error).__name__
    async with deps.txn(deps.tenant_id) as s:
        usable = await has_usable_output(s, deps.run_id)
        target = RunStatus.PARTIAL if usable else RunStatus.FAILED
        status = await lifecycle.end_from_active(
            s,
            deps.tracker(s),
            deps.run_id,
            target,
            reason=reason,
            detail=detail,
            refund_all=not usable,
        )
        created: Any = (
            await s.execute(
                text("SELECT created_at FROM account_runs WHERE id = :r"), {"r": deps.run_id}
            )
        ).scalar_one()
    _emit_root(deps, created, status)


async def _note_interrupted(deps: RunDeps, interrupted: BaseException) -> None:
    """Best effort: record the interruption on the run row; never raises (the process may be
    going away)."""
    from account_intelligence.runs.stop import describe

    try:
        async with deps.txn(deps.tenant_id) as s:
            await lifecycle.note_interruption(
                s, deps.run_id, "interrupted: " + describe(interrupted, limit=560)
            )
    except BaseException:  # noqa: BLE001 - nothing more can be done while stopping
        return


def _workflow_graph(
    deps: RunDeps,
    saver: Any,
    retry_policy: RetryPolicy | None,
    on_enter: Callable[[str], Awaitable[None]],
    tenant_id: UUID,
    run_id: UUID,
) -> tuple[Any, dict[str, Any]]:
    """Workflow v1: the compiled graph and its first state."""
    handlers = build_handlers(deps)
    research = None
    phase_policy = None
    if deps.spec.settings.get("execution_mode") == "parallel":
        from account_intelligence.connectors.service import phase_retry_policy
        from account_intelligence.workflow.parallel_phase import research_node

        research = research_node(handlers, deps)
        if retry_policy is not None:
            phase_policy = phase_retry_policy(
                retry_policy, int(deps.spec.settings.get("max_calls", 60))
            )
    graph = build_graph(
        handlers,
        deps.spec,
        retry_policy=retry_policy,
        on_enter=on_enter,
        research=research,
        phase_retry_policy=phase_policy,
    ).compile(checkpointer=saver)
    first = {
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "run_id": str(run_id),
        "graph_version": str(deps.spec.version),
        "status": RunStatus.RUNNING.value,
        "completed_stages": [],
        "references": {"prompts_hash": prompts_hash()},
        "counters": {},
        "loop_exits": {},
        "coverage_updates": {},
    }
    return graph, first


async def _end(
    deps: RunDeps,
    scheduler: RunScheduler,
    run_id: UUID,
    snap: Any,
    settings: SliceSettings,
    graph_factory: GraphFactory | None,
) -> SliceOutcome:
    """After the graph stopped: wait at an interrupt, or end the run once (one settlement)."""
    tenant_id = deps.tenant_id
    if snap.next:  # interrupted at the approval gate or at a cap pause (D-45)
        kind = "approval"
        for task in snap.tasks:
            for pending_interrupt in task.interrupts:
                value = pending_interrupt.value
                if isinstance(value, dict) and value.get("awaiting") == "cap":
                    kind = "cap"
        hours = settings.approval_hours
        if kind == "cap":
            from spend.config import load_v2_config

            hours = float(load_v2_config().pause_timeout_hours)
        deadline = deps.clock.now() + timedelta(hours=hours)
        async with deps.txn(deps.tenant_id) as s:
            await lifecycle.move(
                s,
                run_id,
                RunStatus.RUNNING,
                RunStatus.AWAITING_APPROVAL,
                approval_deadline=deadline,
                awaiting_kind=kind,
            )
            # the slice that resumes this wait is not research time (slice ceiling, F4)
            await lifecycle.note_pause(s, run_id)
        try:
            await scheduler.schedule_timeout(tenant_id, run_id, deadline)
        except Exception as error:  # noqa: BLE001 - the wait is already recorded
            # the deadline is on the run row: the sweeper (``runs.recovery``) expires a wait
            # whose timeout task was never created, so a failed arm costs time, not the run (F6)
            _log.warning("approval.timeout_not_armed", extra={"error_class": type(error).__name__})
        return SliceOutcome("awaiting", RunStatus.AWAITING_APPROVAL.value)
    if graph_factory is not None:
        target, reason, refund = graph_factory.target(dict(snap.values))
    else:
        (target, reason), refund = _target(dict(snap.values)), False
    current = await _status(deps)
    if current in TERMINAL:
        return SliceOutcome("done", current.value)
    if current is not RunStatus.VERIFYING and target is not RunStatus.FAILED:
        # every path to a non-failed end passes DISTILLING and VERIFYING (transition table)
        async with deps.txn(deps.tenant_id) as s:
            if current is RunStatus.RUNNING:
                await lifecycle.move(s, run_id, RunStatus.RUNNING, RunStatus.DISTILLING)
                current = RunStatus.DISTILLING
            if current is RunStatus.DISTILLING:
                await lifecycle.move(s, run_id, RunStatus.DISTILLING, RunStatus.VERIFYING)
    final = await _finish(deps, target, reason, refund)
    return SliceOutcome("done", final)


async def approve(deps: RunDeps, scheduler: RunScheduler, slice_no: int) -> None:
    """Approval endpoint body: AWAITING_APPROVAL to RUNNING, then the next slice resumes."""
    async with deps.txn(deps.tenant_id) as s:
        await lifecycle.move(s, deps.run_id, RunStatus.AWAITING_APPROVAL, RunStatus.RUNNING)
    await scheduler.enqueue_slice(deps.tenant_id, deps.run_id, slice_no)
