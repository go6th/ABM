"""Expire, never delete (RF2): an expired signal's fact gets ``superseded_at`` (set once; written
by the grading repo, the one writer of facts) and an insert-only ``signal_history`` row with the
reason and the run that expired it, in one transaction. Current tables and counts exclude it (they
read ``superseded_at IS NULL``); history stays queryable. The one writer of ``signal_history``.

GUARANTEE(test_expired_signals_move_to_history_and_are_never_deleted)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.grading import repo as grading_repo

REASONS = ("shelf_life", "removed_after_probes", "superseded_in_refresh")


async def expire(
    s: Any, *, fact_id: UUID, signal_type: str, reason: str, run_id: UUID, now: datetime
) -> bool:
    """True when this call expired the fact (False when it was already superseded)."""
    if reason not in REASONS:
        raise ValueError("unknown expiry reason")
    account_id = await grading_repo.expire(s, fact_id, now)
    if account_id is None:
        return False
    await s.execute(
        text(
            "INSERT INTO signal_history (fact_id, account_id, signal_type, reason, expired_by_run,"
            " expired_at) VALUES (:f, :a, :t, :r, :run, :now)"
        ),
        {
            "f": fact_id,
            "a": account_id,
            "t": signal_type,
            "r": reason,
            "run": run_id,
            "now": now,
        },
    )
    return True


async def listing(s: Any, account_id: UUID, limit: int = 200) -> list[dict[str, Any]]:
    """An account's history, newest first."""
    rows = (
        await s.execute(
            text(
                "SELECT h.fact_id, h.signal_type, h.reason, h.expired_by_run, h.expired_at,"
                " f.attribute_key, f.product_grade FROM signal_history h JOIN facts f ON f.id ="
                " h.fact_id"
                " WHERE h.account_id = :a ORDER BY h.id DESC LIMIT :n"
            ),
            {"a": account_id, "n": limit},
        )
    ).all()
    return [
        {
            "fact_id": str(r.fact_id),
            "signal_type": r.signal_type,
            "reason": r.reason,
            "expired_by_run": str(r.expired_by_run),
            "expired_at": r.expired_at.isoformat(),
            "attribute": r.attribute_key,
            "grade": r.product_grade,
        }
        for r in rows
    ]
