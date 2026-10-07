"""Run feedback (PL1): a member's verdict on one account or one table row, a reason from
``config/pilot.yaml`` and an optional note. The note is tenant data: stored, never logged, never
in an event. Insert-only; the newest row per (user, target) is the current verdict. The one writer
of ``run_feedback``.

GUARANTEE(test_feedback_is_tenant_data_with_a_fixed_reason_list)
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import text

from account_intelligence.config import CONFIG_DIR


class FeedbackRefused(ValueError):
    """Feedback that must be refused; fixed reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@lru_cache
def policy() -> dict[str, Any]:
    """``config/pilot.yaml``."""
    data: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "pilot.yaml").read_text())
    return data


async def record(
    s: Any,
    *,
    account_id: UUID,
    run_id: UUID | None,
    table_kind: str | None,
    row_id: str | None,
    verdict: str,
    reason_code: str,
    note: str | None,
    user_id: str,
    now: datetime,
) -> int:
    """One verdict; a row target must be a materialised row of that run (FeedbackRefused)."""
    cfg = policy()
    if reason_code not in cfg["feedback_reasons"].get(verdict, ()):
        raise FeedbackRefused("unknown_reason")
    if note is not None and len(note) > int(cfg["note_max_chars"]):
        raise FeedbackRefused("note_too_long")
    scope = "account" if row_id is None else "row"
    if scope == "row":
        found = (
            await s.execute(
                text(
                    "SELECT 1 FROM view_rows WHERE run_id = :r AND account_id = :a AND table_kind"
                    " = :k"
                    " AND row_id = :i LIMIT 1"
                ),
                {"r": run_id, "a": account_id, "k": table_kind, "i": row_id},
            )
        ).first()
        if found is None:
            raise FeedbackRefused("unknown_row")
    fid: int = (
        await s.execute(
            text(
                "INSERT INTO run_feedback (account_id, run_id, scope, table_kind, row_id, verdict,"
                " reason_code, note, user_id, at) VALUES (:a, :r, :sc, :k, :i, :v, :why, :n, :u,"
                " :now)"
                " RETURNING id"
            ),
            {
                "a": account_id,
                "r": run_id,
                "sc": scope,
                "k": table_kind if scope == "row" else None,
                "i": row_id,
                "v": verdict,
                "why": reason_code,
                "n": note or None,
                "u": user_id,
                "now": now,
            },
        )
    ).scalar_one()
    return fid


async def listing(s: Any, account_id: UUID | None = None) -> list[dict[str, Any]]:
    """Current verdicts (newest per user and target), newest first."""
    rows = (
        await s.execute(
            text(
                "SELECT DISTINCT ON (f.user_id, f.account_id, f.scope, f.table_kind, f.row_id)"
                " f.id, a.domain, f.run_id, f.scope, f.table_kind, f.row_id, f.verdict,"
                " f.reason_code,"
                " f.note, f.user_id, f.at FROM run_feedback f JOIN accounts a ON a.id ="
                " f.account_id"
                " WHERE (CAST(:a AS uuid) IS NULL OR f.account_id = :a)"
                " ORDER BY f.user_id, f.account_id, f.scope, f.table_kind, f.row_id, f.id DESC"
            ),
            {"a": account_id},
        )
    ).all()
    out = [
        {
            "id": r.id,
            "domain": r.domain,
            "run_id": str(r.run_id) if r.run_id else None,
            "scope": r.scope,
            "table": r.table_kind,
            "row_id": r.row_id,
            "verdict": r.verdict,
            "reason": r.reason_code,
            "note": r.note,
            "user_id": r.user_id,
            "at": r.at.isoformat(),
        }
        for r in rows
    ]
    return sorted(out, key=lambda x: x["id"], reverse=True)[:500]
