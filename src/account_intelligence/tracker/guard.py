"""``finish_run``: the only code path that moves a run to COMPLETED (spec 5.5).

It evaluates the verdict under the run's pinned policy, then updates BOTH run rows (same id) in
the caller's transaction with optimistic ``WHERE status = :expected``. If the caller asked for
COMPLETED and the verdict forbids it, the run ends PARTIAL with the blockers recorded in
``finished_reason``; it never ends COMPLETED. The database triggers from migration 0010 refuse a
COMPLETED with an unseeded, short, unset or swept tracker even if this code were bypassed.

The data-layer ``runs`` table has its own lifecycle vocabulary (queued, running, paused, failed,
completed) and cannot be widened expand-only; ADR-0015 records the mapping: COMPLETED maps to
``completed``, every other terminal state maps to ``failed`` with ``error`` naming the state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.tracker.completion import CompletionVerdict
from account_intelligence.tracker.errors import VersionConflict
from account_intelligence.tracker.service import TrackerService

TERMINAL = frozenset({"COMPLETED", "PARTIAL", "BUDGET_EXCEEDED", "FAILED", "CANCELED"})


@dataclass(frozen=True)
class FinishResult:
    """The terminal status written and the verdict behind it."""

    status: str
    verdict: CompletionVerdict


async def finish_run(
    session: AsyncSession,
    tracker: TrackerService,
    run_id: UUID,
    expected_status: str,
    target_status: str = "COMPLETED",
    reason: str | None = None,
    detail: str | None = None,
) -> FinishResult:
    """Move the run to a terminal state. ``VersionConflict`` if the run is no longer in
    ``expected_status`` (another worker finished it: the caller treats that as a no-op)."""
    if target_status not in TERMINAL:
        raise ValueError(f"{target_status} is not a terminal state")
    verdict = await tracker.verdict(run_id)
    final = target_status
    if target_status == "COMPLETED" and not verdict.can_complete:
        final = "PARTIAL"
        reason = json.dumps(
            {"blocked_by": [b["key"] or b["status"] for b in verdict.blockers]},
            sort_keys=True,
        )
    updated = await session.execute(
        text(
            "UPDATE account_runs SET status = :final, finished_at = now(),"
            " finished_reason = :reason, stop_detail = COALESCE(:detail, stop_detail)"
            " WHERE id = :id AND status = :expected"
        ),
        {
            "final": final,
            "reason": reason,
            "detail": detail,
            "id": run_id,
            "expected": expected_status,
        },
    )
    if updated.rowcount == 0:  # type: ignore[attr-defined]
        raise VersionConflict(f"run is no longer {expected_status}")
    await session.execute(
        text(
            "UPDATE runs SET status = :status, finished_at = now(), error = :error,"
            " started_at = COALESCE(started_at, now())"
            " WHERE id = :id AND status IN ('running', 'paused', 'queued')"
        ),
        {
            "status": "completed" if final == "COMPLETED" else "failed",
            "error": None if final == "COMPLETED" else f"{final}: {reason or 'no reason'}",
            "id": run_id,
        },
    )
    return FinishResult(final, verdict)
