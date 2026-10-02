"""Tracker run guard."""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.tracker.completion import evaluate
from account_intelligence.tracker.errors import VersionConflict
from account_intelligence.tracker.repo import CoverageRepo


async def finish_run(
    session: AsyncSession,
    repo: CoverageRepo,
    run_id: UUID,
    expected_status: str,
    target_status: str = "COMPLETED",
) -> str:
    """Evaluate completion rules and transition run status."""
    rows = await repo.get_all_rows(run_id)
    meta = await repo.get_meta(run_id)
    policy = json.loads(meta["pinned_policy"])

    verdict = evaluate(rows, policy)

    final_status = target_status
    if target_status == "COMPLETED" and not verdict.can_complete:
        final_status = "PARTIAL"

    res = await session.execute(
        text("""
        UPDATE runs
        SET status = :final_status
        WHERE id = :run_id AND status = :expected_status
        """),
        {
            "final_status": final_status.lower(),
            "run_id": run_id,
            "expected_status": expected_status.lower(),
        },
    )
    if res.rowcount == 0:  # type: ignore
        raise VersionConflict("Run status transition failed, expected status mismatch")

    return final_status
