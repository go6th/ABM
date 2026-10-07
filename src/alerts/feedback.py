"""Usefulness of alerts (AL8): in-app ratings with fixed reasons; the metric is useful / rated,
reported with rated / delivered and a small-base label; the 70% target is shown as a TARGET,
never as a result. The one writer of ``alert_feedback``.

GUARANTEE(test_alert_usefulness_is_measured_with_its_base)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

REASONS = {
    "useful": ("acted_on", "new_to_me", "good_timing"),
    "not_useful": ("already_known", "wrong", "not_relevant", "too_late", "too_many"),
}
TARGET = 0.70  # source: roadmap exit criterion, "target to validate"
MIN_BASE = 20  # proposal: below this the share is labelled small base


async def rate(
    s: Any, *, alert_id: UUID, user_id: str, verdict: str, reason: str, now: datetime
) -> None:
    """One rating (ValueError for an unknown verdict or reason)."""
    if reason not in REASONS.get(verdict, ()):
        raise ValueError("unknown verdict or reason")
    await s.execute(
        text(
            "INSERT INTO alert_feedback (alert_id, user_id, verdict, reason, at)"
            " VALUES (:a, :u, :v, :r, :now)"
        ),
        {"a": alert_id, "u": user_id, "v": verdict, "r": reason, "now": now},
    )


async def metric(s: Any) -> dict[str, Any]:
    """The context tenant's usefulness (newest rating per user and alert)."""
    row = (
        await s.execute(
            text(
                "WITH latest AS (SELECT DISTINCT ON (alert_id, user_id) verdict FROM alert_feedback"
                " ORDER BY alert_id, user_id, id DESC) SELECT"
                " (SELECT count(*) FROM latest WHERE verdict = 'useful') AS useful,"
                " (SELECT count(*) FROM latest) AS rated,"
                " (SELECT count(DISTINCT alert_id) FROM alert_outbox WHERE status = 'sent') AS"
                " delivered"
            )
        )
    ).one()
    useful, rated, delivered = int(row.useful), int(row.rated), int(row.delivered)
    return {
        "useful": useful,
        "rated": rated,
        "delivered": delivered,
        "share": round(useful / rated, 4) if rated else None,
        "small_base": rated < MIN_BASE,
        "target": TARGET,
        "target_is_a_result": False,
    }
