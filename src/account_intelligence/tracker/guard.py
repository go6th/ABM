"""``finish_run``: the only code path that moves a run to COMPLETED (spec 5.5).

It evaluates the verdict under the run's pinned policy (or takes the one the caller already
evaluated: ``runs.lifecycle.finish`` evaluates it exactly once), then updates BOTH run rows (same
id) in the caller's transaction with optimistic ``WHERE status = :expected``. If the caller asked
for COMPLETED and the verdict forbids it, the run ends PARTIAL with the reason code
``tracker_verdict`` and the blocking rows in ``stop_detail``; it never ends COMPLETED. The
verdict is the single owner of "Completed" (ADR-0128): the research graph's deliver step reports
facts (files withheld, a failed pipeline) and never votes on the terminal status. The database
triggers from migration 0010 refuse a COMPLETED with an unseeded, short, unset or swept tracker
even if this code were bypassed.

The data-layer ``runs`` table has its own lifecycle vocabulary (queued, running, paused, failed,
completed) and cannot be widened expand-only; ADR-0015 records the mapping: COMPLETED maps to
``completed``, every other terminal state maps to ``failed`` with ``error`` naming the state.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.tracker.completion import CompletionVerdict
from account_intelligence.tracker.errors import VersionConflict
from account_intelligence.tracker.service import TrackerService

TERMINAL = frozenset({"COMPLETED", "PARTIAL", "BUDGET_EXCEEDED", "FAILED", "CANCELED"})
# the reason code of a run asked to end COMPLETED that the tracker's verdict refused (ADR-0128)
TRACKER_VERDICT = "tracker_verdict"


def terminal_status(target_status: str, verdict: CompletionVerdict) -> str:
    """The one mapping from a requested terminal state and the verdict to the stored state:
    COMPLETED only when the verdict allows it, PARTIAL otherwise; any other target unchanged."""
    if target_status == "COMPLETED" and not verdict.can_complete:
        return "PARTIAL"
    return target_status


def blocked_detail(verdict: CompletionVerdict) -> str:
    """The rows (or tracker states) that kept the run from COMPLETED, for ``stop_detail``."""
    keys = [str(b["key"] or b["status"]) for b in verdict.blockers]
    more = f" and {len(keys) - 12} more" if len(keys) > 12 else ""
    return ("blocked_by: " + ", ".join(keys[:12]) + more)[:600]


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
    verdict: CompletionVerdict | None = None,
) -> FinishResult:
    """Move the run to a terminal state. ``VersionConflict`` if the run is no longer in
    ``expected_status`` (another worker finished it: the caller treats that as a no-op).
    ``verdict`` is the one the caller evaluated (never evaluated twice); None evaluates it here."""
    if target_status not in TERMINAL:
        raise ValueError(f"{target_status} is not a terminal state")
    if verdict is None:
        verdict = await tracker.verdict(run_id)
    final = terminal_status(target_status, verdict)
    if final != target_status:
        reason = TRACKER_VERDICT
        detail = detail or blocked_detail(verdict)
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
