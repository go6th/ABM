"""Orphan recovery: end runs whose worker died without ending them.

A worker that is killed (OOM, deploy, crash) leaves its run RUNNING, DISTILLING or VERIFYING with
an expired lease and its data in place. ``sweep`` finds those runs through the narrow definer
function ``runs_orphaned`` (identifiers only, migration 0050) and, under each run's own tenant
context, ends it ``worker_lost``: PARTIAL when it holds a finished paid call, a fact or a
deliverable, FAILED with a full refund otherwise. Nothing is deleted; a PARTIAL run's pulls stay
reusable through the research cache and ``run_input {"resume_from": ...}``.

The sweep runs once at worker startup and every ``SWEEP_INTERVAL_S`` (``api.main`` lifespan,
worker role); ``ORPHAN_AFTER_S`` is how long a lease must be dead. A run waiting for a person
(AWAITING_APPROVAL) is never swept. One pass is bounded by ``batch_size`` runs.

``mark_stalled`` (owner decision 2026-10-14) finds running runs whose heartbeat
(``account_runs.progress_at``, moved by every ledger event and model call) has not moved for the
platform's ``stall_after_s``: each is marked stalled once and its owner is told in the app
("research_stalled"); the screen then offers Resume, which ends it PARTIAL and continues it.

GUARANTEE(test_the_sweeper_ends_an_orphaned_run_partial_and_keeps_its_data)
GUARANTEE(test_a_stalled_run_is_marked_once_and_its_owner_told)
GUARANTEE(test_the_sweeper_leaves_live_and_waiting_runs_alone)
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.runs import lifecycle
from account_intelligence.runs.stop import has_usable_output
from account_intelligence.runs.transitions import TERMINAL, RunStatus
from account_intelligence.tracker.service import TrackerService

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
TrackerFor = Callable[[AsyncSession], TrackerService]

REASON = "worker_lost"


@dataclass
class SweepResult:
    """What one pass did (identifiers only)."""

    partial: list[UUID] = field(default_factory=list)
    failed: list[UUID] = field(default_factory=list)
    skipped: list[UUID] = field(default_factory=list)
    failed_to_end: list[UUID] = field(default_factory=list)
    truncated: bool = False


async def sweep(
    sessions: SessionFactory,
    txn: TxnFactory,
    tracker_for: TrackerFor,
    now: datetime,
    *,
    orphan_after_s: float,
    batch_size: int = 200,
    max_pages: int = 25,
) -> SweepResult:
    """One bounded pass (see the module docstring)."""
    out = SweepResult()
    stale_before = now - timedelta(seconds=orphan_after_s)
    # page through every orphan (oldest first): a run that cannot be ended stays listed, and must
    # never hide the ones after it; at most max_pages pages per pass
    rows: list[Any] = []
    # LOOP(orphan listing pages): cap=max_pages pages of batch_size; exit=short page
    for page in range(max_pages):
        async with sessions() as s:
            got = (
                await s.execute(
                    text("SELECT tenant_id, run_id FROM runs_orphaned(:before) LIMIT :n OFFSET :o"),
                    {"before": stale_before, "n": batch_size, "o": page * batch_size},
                )
            ).all()
        rows += got
        if len(got) < batch_size:
            break
    else:
        out.truncated = True
    # LOOP(orphan sweep): cap=max_pages * batch_size runs per pass; exit=rows exhausted
    for row in rows:
        tenant_id, run_id = UUID(str(row.tenant_id)), UUID(str(row.run_id))
        try:
            final = await _end_one(txn, tracker_for, tenant_id, run_id, stale_before)
        except Exception:  # noqa: BLE001 - one bad run never stops the pass; it is retried next time
            out.failed_to_end.append(run_id)
            continue
        if final is None:
            out.skipped.append(run_id)
        else:
            (out.partial if final == RunStatus.PARTIAL.value else out.failed).append(run_id)
    return out


async def _end_one(
    txn: TxnFactory, tracker_for: TrackerFor, tenant_id: UUID, run_id: UUID, stale_before: datetime
) -> str | None:
    """End one orphaned run; None when it is no longer an orphan (or not readable here)."""
    async with txn(tenant_id) as s:
        raw = (
            # FOR UPDATE: a slice that begins in this second waits for the verdict and then reads a
            # terminal run, instead of driving a run the sweeper is ending (staging 2026-10-30)
            await s.execute(
                text("SELECT status FROM account_runs WHERE id = :r FOR UPDATE"), {"r": run_id}
            )
        ).scalar_one_or_none()
        if raw is None:
            return None
        status = RunStatus(str(raw))
        if status in TERMINAL or status is RunStatus.AWAITING_APPROVAL:
            return None
        live = (
            await s.execute(
                text("SELECT 1 FROM run_leases WHERE run_id = :r AND expires_at >= :b"),
                {"r": run_id, "b": stale_before},
            )
        ).first()
        if live is not None:  # a worker took it between the listing and now
            return None
        usable = await has_usable_output(s, run_id)
        return await lifecycle.end_from_active(
            s,
            tracker_for(s),
            run_id,
            RunStatus.PARTIAL if usable else RunStatus.FAILED,
            reason=REASON,
            detail=(
                "worker_lost: the worker's lease expired before "
                f"{stale_before.isoformat()} and no worker resumed the run"
            ),
            refund_all=not usable,
        )


async def mark_stalled(
    sessions: SessionFactory,
    txn: TxnFactory,
    now: datetime,
    *,
    batch_size: int = 200,
    notify_owner: bool = True,
) -> list[UUID]:
    """Mark stalled runs and tell their owners (see the module docstring); their ids. The
    scheduled sweep continues a stalled research by itself right after this
    (``replica.auto_resume``); the owner is still told it stalled, then that it was continued."""
    from account_intelligence.connectors.spend_guard import limits
    from account_intelligence.replica.notifications import notify

    async with sessions() as s:
        stall_after = (await limits(s)).stall_after_s
        rows = (
            await s.execute(
                text("SELECT tenant_id, run_id FROM runs_stalled(:b) LIMIT :n"),
                {"b": now - timedelta(seconds=stall_after), "n": batch_size},
            )
        ).all()
    marked: list[UUID] = []
    # LOOP(stall marking): cap=batch_size runs per pass; exit=rows exhausted
    for row in rows:
        tenant_id, run_id = UUID(str(row.tenant_id)), UUID(str(row.run_id))
        async with txn(tenant_id) as s:
            hit = await lifecycle.mark_stalled(s, run_id)
            if hit is None:
                continue
            job = await s.scalar(
                text("SELECT id FROM research_jobs WHERE run_id = :r"), {"r": run_id}
            )
            if (
                notify_owner
                and hit.owner_user_id
                and not str(hit.owner_user_id).startswith("system:")
            ):
                await notify(
                    s,
                    user_id=str(hit.owner_user_id),
                    kind="research_stalled",
                    account_id=hit.account_id,
                    job_id=job,
                )
        marked.append(run_id)
    return marked
