"""One account run to completion in one container (Cloud Run Jobs; ``python -m
account_intelligence.worker``).

The job drives the same slice lifecycle the Cloud Tasks worker drives (``workflow.runner.
execute_slice``): the checkpoint, the run lease and the ledger are unchanged, so a job that dies
mid-slice is resumed by the next execution from its last checkpoint and paid calls replay at no
cost. Instead of queueing the next slice remotely, the job's ``LoopScheduler`` records it and the
loop runs it here. The loop ends when the run waits for a person (approval, cap pause), reaches a
terminal state, or spends ``max_slices``. A run held by another worker is retried a bounded number
of times with backoff, then the job exits non-zero so the platform retries the execution.

GUARANTEE(test_job_runs_slices_until_terminal): loop contract.
GUARANTEE(test_job_never_exceeds_max_slices): bounded.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from obs.logging import logger

log = logger("worker.job")

STOP = frozenset({"awaiting", "done", "canceled", "noop"})


@dataclass
class LoopScheduler:
    """``RunScheduler`` for a job: the next slice is run by this process, not queued remotely.
    Idempotent per ``(run, slice)`` like every scheduler; timeouts are left to the API side."""

    pending: list[int] = field(default_factory=list)
    seen: set[tuple[UUID, int]] = field(default_factory=set)
    timeouts: dict[UUID, datetime] = field(default_factory=dict)

    async def enqueue_slice(
        self, tenant_id: UUID, run_id: UUID, slice_no: int, delay_s: float = 0.0
    ) -> None:
        """Remember the next slice once."""
        if (run_id, slice_no) in self.seen:
            return
        self.seen.add((run_id, slice_no))
        self.pending.append(slice_no)

    async def schedule_timeout(self, tenant_id: UUID, run_id: UUID, at: datetime) -> None:
        """Record the approval deadline (the API side arms the real timeout task)."""
        self.timeouts.setdefault(run_id, at)


@dataclass(frozen=True)
class JobResult:
    """How the job ended: the last slice outcome, run status and slices run here."""

    outcome: str
    status: str
    slices: int


SliceFn = Callable[[LoopScheduler, int], Awaitable[tuple[str, str]]]


async def drive(
    run_slice: SliceFn,
    first_slice: int,
    *,
    max_slices: int,
    busy_retries: int = 5,
    busy_wait_s: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> JobResult:
    """Run slices from ``first_slice`` until the run stops (see the module docstring)."""
    scheduler = LoopScheduler()
    slice_no, ran, busy = first_slice, 0, 0
    outcome, status = "noop", "UNKNOWN"
    # LOOP(job slices): cap=max_slices; exit=awaiting/done/canceled/noop, no next slice, busy cap
    while ran < max_slices:
        outcome, status = await run_slice(scheduler, slice_no)
        if outcome == "busy":
            busy += 1
            if busy > busy_retries:
                raise RuntimeError("run is held by another worker; the platform retries the job")
            await sleep(busy_wait_s * busy)
            continue
        ran += 1
        if outcome in STOP or not scheduler.pending:
            break
        slice_no = scheduler.pending.pop(0)
    log.info("job.finished", extra={"outcome": outcome, "status": status, "slices": ran})
    return JobResult(outcome, status, ran)


async def run_job(tenant_id: UUID, run_id: UUID) -> JobResult:
    """Build the container, then drive the run from its next unrun slice."""
    from sqlalchemy import text

    from account_intelligence.app.container import build_container
    from account_intelligence.config import load_settings
    from account_intelligence.db.session import tenant_txn
    from account_intelligence.platform.checkpoint import checkpointer_factory
    from account_intelligence.workflow.runner import execute_slice
    from agents.production.wiring import AbmGraphFactory, uses_abm

    settings = load_settings()
    container = build_container()
    connector = None
    if container.env in ("staging", "production"):
        from account_intelligence.db.session import bind_engine
        from account_intelligence.platform.gcp.adapters import cloud_sql_engine

        assert settings.cloud_sql_instance and settings.db_iam_user and settings.db_name
        engine, connector = await cloud_sql_engine(
            settings.cloud_sql_instance, settings.db_iam_user, settings.db_name
        )
        bind_engine(engine)
    try:
        async with tenant_txn(tenant_id) as s:
            used = (
                await s.execute(
                    text("SELECT slices_used FROM account_runs WHERE id = :r"), {"r": run_id}
                )
            ).scalar_one_or_none()
        if used is None:
            raise LookupError("run not found for this tenant")
        deps = await container.deps_for(tenant_id, run_id)
        factory = AbmGraphFactory() if await uses_abm(deps) else None
        checkpointer = checkpointer_factory(container.checkpoint_dsn)

        async def one(scheduler: LoopScheduler, slice_no: int) -> tuple[str, str]:
            """Run a single scheduler slice for a tenant run."""
            out = await execute_slice(
                container.deps_for,
                checkpointer,
                scheduler,
                tenant_id,
                run_id,
                slice_no,
                settings=container.slices,
                retry_policy=container.extras.get("retry_policy"),
                graph_factory=factory,
            )
            return out.outcome, out.status

        return await drive(one, int(used), max_slices=container.slices.max_slices)
    finally:
        if connector is not None:
            await connector.close_async()
