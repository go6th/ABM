"""Runner v2: time-sliced, resumable execution of Workflow v1 (workflow spec 6 and 7).

``execute_slice`` is what the worker endpoint (``/tasks/run-slice``) calls for one slice. The
LangGraph Postgres checkpoint is the source of truth: a slice resumes from the last checkpoint,
so a killed worker loses at most the node in flight, whose paid calls replay from the ledger.

One slice:
1. a terminal run returns at once; an AWAITING_APPROVAL run waits; QUEUED moves to RUNNING; a run
   past ``max_slices`` ends FAILED (platform fault: full refund);
2. the graph runs under ``asyncio.timeout(slice_seconds)``; between supersteps the run status is
   re-read and a CANCELED run stops cleanly (its settlement was made by the cancel path);
3. an approval interrupt moves the run to AWAITING_APPROVAL and arms the 24 hour timeout task
   (defect 8: the timeout cancels and refunds, it never loops into the same interrupt); a cap
   pause interrupt (parallel execution, D-45) waits the same way with ``awaiting_kind = 'cap'``,
   and its timeout records the decline and resumes the run to end BUDGET_EXCEEDED;
4. a slice that runs out of time enqueues the next slice (named task, idempotent) after it
   released the run lease;
5. a finished graph ends the run through ``lifecycle.finish``: BUDGET_EXCEEDED when the cap was
   hit, PARTIAL with the reason when builders or the gate did not pass, else COMPLETED (which the
   tracker guard still refuses if a row is open);
6. an unexpected error ends the run PARTIAL (reason: the error class, plus a redacted detail)
   when it already holds a finished paid call, a fact or a deliverable, and FAILED with a full
   refund only when it holds nothing; it is re-raised for the logs. A cancellation or interrupt
   (SIGTERM, SIGINT) only notes itself on the run, which stays resumable from its checkpoint;
   ``runs.sweeper`` ends it ``worker_lost`` if no worker resumes it.
"""

from __future__ import annotations

import asyncio
import contextlib
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


@dataclass(frozen=True)
class SliceOutcome:
    """What one slice did: ``continue`` (next slice queued), ``awaiting``, ``done``,
    ``canceled``, ``noop`` (nothing to do) or ``busy`` (another worker holds the run)."""

    outcome: str
    status: str


@dataclass(frozen=True)
class SliceSettings:
    """Slice loop limits (skill: 240 s slices, at most 60) and the approval timeout."""

    slice_seconds: float = 240.0
    max_slices: int = 60
    approval_hours: float = 24.0


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


async def _begin(deps: RunDeps, settings: SliceSettings) -> SliceOutcome | None:
    """Step 1. Returns an outcome when the slice should not run the graph."""
    async with deps.txn(deps.tenant_id) as s:
        status = await lifecycle.current_status(s, deps.run_id)
        if status in TERMINAL:
            return SliceOutcome("noop", status.value)
        if status is RunStatus.AWAITING_APPROVAL:
            return SliceOutcome("awaiting", status.value)
        # LOOP(slice loop): cap=max_slices (60) slices of slice_seconds (240); exit=done, awaiting,
        # canceled, slice ceiling (FAILED, full refund)
        used = await lifecycle.begin_slice(s, deps.run_id)
        if status is RunStatus.QUEUED:
            await lifecycle.move(s, deps.run_id, RunStatus.QUEUED, RunStatus.RUNNING)
    if used > settings.max_slices:
        final = await _finish(deps, RunStatus.FAILED, "slice_ceiling", True)
        return SliceOutcome("done", final)
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
    deps = await deps_factory(tenant_id, run_id)
    owner: str | None = None
    if deps.connectors is not None:
        # One worker per run (D-18): the run lease is the mutex for the whole slice, not only for
        # paid calls. A second delivery of a slice (redelivery, overlap) yields; the worker
        # endpoint answers 503 so Cloud Tasks redelivers it later.
        try:
            await deps.connectors.ledger.acquire_lease(
                deps.call_context(), deps.connectors.worker_id
            )
        except LeaseUnavailable:
            return SliceOutcome("busy", (await _status(deps)).value)
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
    if outcome.outcome == "continue":
        # only after the release: the next slice takes a fresh lease epoch, so a call this slice's
        # timeout cut off reads as abandoned, never as still in flight
        await scheduler.enqueue_slice(tenant_id, run_id, slice_no + 1)
    return outcome


CANCEL_POLL_S = 10.0


async def _drive(graph: Any, graph_input: Any, config: Mapping[str, Any], deps: RunDeps) -> bool:
    """Run the graph; True when the run was canceled. The status is read after every update and
    every ``CANCEL_POLL_S`` inside a long node, so a canceled run stops calling providers within
    seconds instead of when its node ends."""

    async def stream() -> bool:
        async for _ in graph.astream(graph_input, config, stream_mode="updates"):
            if await _status(deps) is RunStatus.CANCELED:
                return True
        return False

    task = asyncio.create_task(stream())
    try:
        # LOOP(cancel watch): cap=the slice timeout around it; exit=graph done, canceled, timeout
        while True:
            done, _ = await asyncio.wait({task}, timeout=CANCEL_POLL_S)
            if done:
                return task.result()
            if await _status(deps) is RunStatus.CANCELED:
                return True
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
    early = await _begin(deps, settings)
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
            elif pending:
                graph_input = Command(resume=True)  # the run was approved (status is RUNNING)
            else:
                graph_input = None
            try:
                async with asyncio.timeout(settings.slice_seconds):
                    if await _drive(graph, graph_input, config, deps):
                        return SliceOutcome("canceled", RunStatus.CANCELED.value)
            except (TimeoutError, SlotUnavailable):
                # a slot the governor could not grant in its bounded wait is queueing, not a
                # failure: the slice ends like a timeout and the next one re-runs the node
                return SliceOutcome("continue", (await _status(deps)).value)
            snap = await graph.aget_state(config)
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
        await scheduler.schedule_timeout(tenant_id, run_id, deadline)
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
