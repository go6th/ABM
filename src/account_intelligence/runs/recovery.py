"""Hand-off recovery: start what was committed but never started (orchestration review F6).

Four places commit a state and only then call Cloud Tasks, outside the transaction: a confirmed
research job (``research_jobs`` queued, then ``enqueue_research_job``), the job's run (QUEUED, then
slice 0), a continuation job reserved by auto-resume, and a run's wait for a person (the status
moves to AWAITING_APPROVAL, then the timeout task is created). A process killed between the two
steps, or an enqueue that failed, left a job or run that nothing ever started and, for a job, an
account that stayed blocked behind it.

``recover`` runs in every sweeper pass, one client at a time, and is idempotent: every hand-off it
repeats is a named task (``job-<id>``, ``run-<id>-slice-<n>``), so a task that still exists makes
the repeat a no-op, and a job or slice that is started twice is claimed once (``claim`` is an
atomic queued-to-running move; ``begin_slice`` is an atomic slice counter). Cloud Tasks keeps the
name of a task that ran or was deleted for about an hour, so a repeat of a hand-off whose task
already ran lands at most an hour late: late, never lost.

1. a job ``queued`` for more than ``after_s``: handed to the worker again;
2. a job ``running`` more than ``after_s`` with no run (the worker died between the claim and the
   run's creation): put back to ``queued`` and handed over again (the run is created under the
   job's own idempotency key, so it exists once);
3. a run QUEUED for more than ``after_s`` that never began slice 0: slice 0 is enqueued again;
4. a run AWAITING_APPROVAL more than ``grace_s`` past its deadline (its timeout task was never
   created, or never ran): expired the way the timeout task does, a first-cap approval is
   cancelled and refunded, a cap pause is resumed and its next slice enqueued.

GUARANTEE(test_a_job_that_was_committed_but_never_started_is_started_again)
GUARANTEE(test_a_run_that_never_began_slice_zero_is_started_again)
GUARANTEE(test_a_wait_past_its_deadline_is_expired_by_the_sweeper)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.ports import RunScheduler
from account_intelligence.runs import lifecycle
from account_intelligence.tracker.service import TrackerService
from obs.logging import logger

_log = logger(__name__)

StartJob = Callable[[UUID, UUID], Awaitable[None]]
TrackerFor = Callable[[Any], TrackerService]


@dataclass
class RecoveryResult:
    """What one pass started again (identifiers only)."""

    jobs_started: list[UUID] = field(default_factory=list)
    jobs_requeued: list[UUID] = field(default_factory=list)
    runs_started: list[UUID] = field(default_factory=list)
    waits_cancelled: list[UUID] = field(default_factory=list)
    waits_resumed: list[UUID] = field(default_factory=list)
    failed: list[UUID] = field(default_factory=list)

    @property
    def total(self) -> int:
        """How many hand-offs the pass repeated."""
        return (
            len(self.jobs_started)
            + len(self.runs_started)
            + len(self.waits_cancelled)
            + len(self.waits_resumed)
        )


async def recover(
    tenants: Sequence[UUID],
    start_job: StartJob,
    scheduler: RunScheduler,
    tracker_for: TrackerFor,
    now: datetime,
    *,
    after_s: float = 600.0,
    grace_s: float = 900.0,
    limit: int = 50,
) -> RecoveryResult:
    """One bounded pass over the given clients (see the module docstring)."""
    out = RecoveryResult()
    before = now - timedelta(seconds=after_s)
    late = now - timedelta(seconds=grace_s)
    # LOOP(hand-off recovery clients): cap=every client once per pass, `limit` items per kind;
    # exit=last client
    for tenant in tenants:
        try:
            await _one_client(
                tenant, start_job, scheduler, tracker_for, now, before, late, limit, out
            )
        except Exception as error:  # noqa: BLE001 - one client never stops the pass
            _log.warning(
                "recovery.client_failed",
                extra={"tenant_id": str(tenant), "error_class": type(error).__name__},
            )
    if out.total:
        _log.info(
            "recovery.handoffs",
            extra={
                "count": out.total,
                "jobs": len(out.jobs_started),
                "runs": len(out.runs_started),
                "waits": len(out.waits_cancelled) + len(out.waits_resumed),
            },
        )
    return out


async def _one_client(
    tenant: UUID,
    start_job: StartJob,
    scheduler: RunScheduler,
    tracker_for: TrackerFor,
    now: datetime,
    before: datetime,
    late: datetime,
    limit: int,
    out: RecoveryResult,
) -> None:
    async with tenant_txn(tenant, system=True) as s:
        # a job whose worker died between the claim and the run: back to the queue first
        # (research_jobs has one writer, replica/credits.py)
        from account_intelligence.replica import credits as CR

        stuck = await CR.requeue_unstarted(s, before, limit)
        out.jobs_requeued += stuck
        jobs = [
            UUID(str(r.id))
            for r in (
                await s.execute(
                    text(
                        "SELECT id FROM research_jobs WHERE status = 'queued' AND created_at < :b"
                        " ORDER BY created_at LIMIT :n"
                    ),
                    {"b": before, "n": limit},
                )
            ).all()
        ]
        # a run that never began its first slice, unless its job already ended
        runs = [
            UUID(str(r.id))
            for r in (
                await s.execute(
                    text(
                        "SELECT r.id FROM account_runs r WHERE r.status = 'QUEUED'"
                        " AND r.slices_used = 0 AND r.created_at < :b"
                        " AND NOT EXISTS (SELECT 1 FROM research_jobs j WHERE j.run_id = r.id"
                        "   AND j.status IN ('done', 'failed', 'canceled'))"
                        " ORDER BY r.created_at LIMIT :n"
                    ),
                    {"b": before, "n": limit},
                )
            ).all()
        ]
        waits = [
            (UUID(str(r.id)), str(r.owner_user_id))
            for r in (
                await s.execute(
                    text(
                        "SELECT id, owner_user_id FROM account_runs"
                        " WHERE status = 'AWAITING_APPROVAL'"
                        " AND approval_deadline IS NOT NULL AND approval_deadline < :late"
                        " ORDER BY approval_deadline LIMIT :n"
                    ),
                    {"late": late, "n": limit},
                )
            ).all()
        ]
    for job in jobs:
        try:
            await start_job(tenant, job)
            out.jobs_started.append(job)
        except Exception as error:  # noqa: BLE001 - tried again next pass
            out.failed.append(job)
            _log.warning("recovery.job_failed", extra={"error_class": type(error).__name__})
    for run in runs:
        try:
            await scheduler.enqueue_slice(tenant, run, 0)
            out.runs_started.append(run)
        except Exception as error:  # noqa: BLE001 - tried again next pass
            out.failed.append(run)
            _log.warning("recovery.run_failed", extra={"error_class": type(error).__name__})
    for run, owner in waits:
        try:
            await _expire_wait(tenant, run, owner, scheduler, tracker_for, now, out)
        except Exception as error:  # noqa: BLE001 - tried again next pass
            out.failed.append(run)
            _log.warning("recovery.wait_failed", extra={"error_class": type(error).__name__})


async def _expire_wait(
    tenant: UUID,
    run: UUID,
    owner: str,
    scheduler: RunScheduler,
    tracker_for: TrackerFor,
    now: datetime,
    out: RecoveryResult,
) -> None:
    """The approval-timeout task's work (``/tasks/approval-timeout``) for a wait whose task never
    ran, as the run's owner like that task: a first-cap approval is cancelled and refunded; a cap
    pause resumes the run."""
    async with tenant_txn(tenant, user=owner, system=True) as s:
        expired = await lifecycle.expire_approval(s, tracker_for(s), run, now)
        resumed = False if expired else await lifecycle.expire_cap_pause(s, run, now)
        used = await lifecycle.slices_used(s, run) if resumed else 0
    if expired:
        out.waits_cancelled.append(run)
    if resumed:
        await scheduler.enqueue_slice(tenant, run, used)
        out.waits_resumed.append(run)
