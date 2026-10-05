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
   (defect 8: the timeout cancels and refunds, it never loops into the same interrupt);
4. a slice that runs out of time enqueues the next slice (named task, idempotent);
5. a finished graph ends the run through ``lifecycle.finish``: BUDGET_EXCEEDED when the cap was
   hit, PARTIAL with the reason when builders or the gate did not pass, else COMPLETED (which the
   tracker guard still refuses if a row is open);
6. an unexpected error ends the run FAILED with a full refund and is re-raised for the logs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from langgraph.types import Command, RetryPolicy

from account_intelligence.agents.prompts import prompts_hash
from account_intelligence.connectors.errors import LeaseUnavailable
from account_intelligence.platform.ports import RunScheduler
from account_intelligence.runs import lifecycle
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.engine import build_graph
from account_intelligence.workflow.handlers import build_handlers
from account_intelligence.workflow.transitions import TERMINAL, RunStatus

DepsFactory = Callable[[UUID, UUID], Awaitable[RunDeps]]
Checkpointer = Callable[[UUID], AbstractAsyncContextManager[Any]]

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
    return status


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
) -> SliceOutcome:
    """Run one slice of one run (see the module docstring)."""
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
        return await _slice(
            deps, checkpointer, scheduler, tenant_id, run_id, slice_no, settings, retry_policy
        )
    finally:
        if owner is not None and deps.connectors is not None:
            await deps.connectors.ledger.release_lease(deps.call_context(), owner)


async def _slice(
    deps: RunDeps,
    checkpointer: Checkpointer,
    scheduler: RunScheduler,
    tenant_id: UUID,
    run_id: UUID,
    slice_no: int,
    settings: SliceSettings,
    retry_policy: RetryPolicy | None,
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
            graph = build_graph(
                build_handlers(deps), deps.spec, retry_policy=retry_policy, on_enter=on_enter
            ).compile(checkpointer=saver)
            snap = await graph.aget_state(config)
            pending = bool(snap.next) and any(t.interrupts for t in snap.tasks)
            graph_input: Any
            if not snap.values:
                graph_input = {
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
            elif pending:
                graph_input = Command(resume=True)  # the run was approved (status is RUNNING)
            else:
                graph_input = None
            try:
                async with asyncio.timeout(settings.slice_seconds):
                    async for _ in graph.astream(graph_input, config, stream_mode="updates"):
                        if await _status(deps) is RunStatus.CANCELED:
                            return SliceOutcome("canceled", RunStatus.CANCELED.value)
            except TimeoutError:
                await scheduler.enqueue_slice(tenant_id, run_id, slice_no + 1)
                return SliceOutcome("continue", (await _status(deps)).value)
            snap = await graph.aget_state(config)
    except Exception as error:
        if (await _status(deps)) not in TERMINAL:
            await _finish(deps, RunStatus.FAILED, type(error).__name__, True)
        raise
    if snap.next:  # interrupted at the approval gate
        deadline = deps.clock.now() + timedelta(hours=settings.approval_hours)
        async with deps.txn(deps.tenant_id) as s:
            await lifecycle.move(
                s,
                run_id,
                RunStatus.RUNNING,
                RunStatus.AWAITING_APPROVAL,
                approval_deadline=deadline,
            )
        await scheduler.schedule_timeout(tenant_id, run_id, deadline)
        return SliceOutcome("awaiting", RunStatus.AWAITING_APPROVAL.value)
    target, reason = _target(dict(snap.values))
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
    final = await _finish(deps, target, reason, False)
    return SliceOutcome("done", final)


async def approve(deps: RunDeps, scheduler: RunScheduler, slice_no: int) -> None:
    """Approval endpoint body: AWAITING_APPROVAL to RUNNING, then the next slice resumes."""
    async with deps.txn(deps.tenant_id) as s:
        await lifecycle.move(s, deps.run_id, RunStatus.AWAITING_APPROVAL, RunStatus.RUNNING)
    await scheduler.enqueue_slice(deps.tenant_id, deps.run_id, slice_no)
