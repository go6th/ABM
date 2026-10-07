"""The seller usefulness metric (PL1, PL2, D-68): computed from events with its base, never
stored. Per tenant: the share of delivered first-call questions used and of delivered Confirmed
facts referenced, with numerator, base and a ``small_base`` label when the base is below the
configured minimum (no rate is shown as a finding on a small base). Operator aggregate: per-tenant
counts through the definer function ``pilot_aggregate`` (no content).

GUARANTEE(test_the_usefulness_metric_carries_its_base)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from pilot.feedback import policy


def _rate(used: int, base: int, min_base: int) -> dict[str, Any]:
    return {
        "used": used,
        "base": base,
        "small_base": base < min_base,
        "rate": round(used / base, 4) if base else None,
    }


async def tenant_metric(s: Any, now: datetime) -> dict[str, Any]:
    """The context tenant's metric over the configured window."""
    cfg = policy()["metric"]
    since = now - timedelta(days=int(cfg["window_days"]))
    params = {"since": since}
    q_base = (
        await s.execute(
            text(
                "SELECT count(DISTINCT r.id) FROM report_sections r JOIN account_runs ar"
                " ON ar.id = r.run_id WHERE r.kind = 'first_call_question' AND ar.created_at >="
                " :since"
                " AND r.version = (SELECT max(x.version) FROM report_sections x WHERE x.run_id ="
                " r.run_id)"
            ),
            params,
        )
    ).scalar_one()
    q_used = (
        await s.execute(
            text(
                "SELECT count(DISTINCT e.subject_id) FROM usefulness_events e JOIN"
                " report_sections r"
                " ON r.id = e.subject_id JOIN account_runs ar ON ar.id = r.run_id"
                " WHERE e.kind = 'first_call_question_used' AND ar.created_at >= :since"
                " AND r.version = (SELECT max(x.version) FROM report_sections x WHERE x.run_id ="
                " r.run_id)"
            ),
            params,
        )
    ).scalar_one()
    f_base = (
        await s.execute(
            text(
                "SELECT count(*) FROM facts WHERE product_grade = 'confirmed' AND superseded_at"
                " IS NULL"
                " AND visibility = 'public' AND created_at >= :since"
            ),
            params,
        )
    ).scalar_one()
    f_used = (
        await s.execute(
            text(
                "SELECT count(DISTINCT e.subject_id) FROM usefulness_events e JOIN facts f"
                " ON f.id = e.subject_id WHERE e.kind = 'confirmed_fact_referenced'"
                " AND f.product_grade = 'confirmed' AND f.superseded_at IS NULL"
                " AND f.visibility = 'public' AND f.created_at >= :since"
            ),
            params,
        )
    ).scalar_one()
    fb = (
        await s.execute(
            text(
                "SELECT verdict, reason_code, count(*) AS n FROM run_feedback WHERE at >= :since"
                " GROUP BY verdict, reason_code ORDER BY verdict, reason_code"
            ),
            params,
        )
    ).all()
    min_base = int(cfg["min_base"])
    return {
        "window_days": int(cfg["window_days"]),
        "status": "proposal",
        "question_use": _rate(int(q_used), int(q_base), min_base),
        "confirmed_fact_reference": _rate(int(f_used), int(f_base), min_base),
        "feedback": [
            {"verdict": r.verdict, "reason": r.reason_code, "count": int(r.n)} for r in fb
        ],
    }


async def operator_aggregate(plain: Any, now: datetime) -> list[dict[str, Any]]:
    """Per-tenant counts (identifiers and numbers only)."""
    cfg = policy()["metric"]
    rows = (
        await plain.execute(
            text("SELECT * FROM pilot_aggregate(:since)"),
            {"since": now - timedelta(days=int(cfg["window_days"]))},
        )
    ).all()
    return [
        {
            "tenant_id": str(r.tenant_id),
            "questions_used": int(r.questions_used),
            "facts_referenced": int(r.facts_referenced),
            "useful": int(r.useful),
            "not_useful": int(r.not_useful),
        }
        for r in rows
    ]
