"""Tracker rendering."""

from __future__ import annotations

from typing import Any

from account_intelligence.tracker.config import load_tracker_config
from account_intelligence.tracker.gap import TrackerRowState
from account_intelligence.tracker.tally import sort_for_rendering, tally


def render_client_view(rows: list[TrackerRowState]) -> dict[str, Any]:
    """Client-safe view (allow-list)."""
    sorted_rows = sort_for_rendering(rows)
    config = load_tracker_config()

    rendered_rows = []
    for r in sorted_rows:
        reason_text = None
        if r.reason_code:
            reason_text = config.reason_codes.get(r.reason_code.value, "Unknown reason")

        rendered_rows.append(
            {
                "key": r.key,
                "status": r.status.value if r.status else "unset",
                "priority": r.priority,
                "reason": reason_text,
                # tool category is not on TrackerRowState; it would come from the proof
            }
        )

    return {
        "rows": rendered_rows,
        "tally": tally(rows),  # T11
    }


def render_operator_view(rows: list[TrackerRowState], proofs: dict[str, Any]) -> dict[str, Any]:
    """Operator view includes internal details."""
    sorted_rows = sort_for_rendering(rows)
    rendered_rows = []
    for r in sorted_rows:
        rendered_rows.append(
            {
                "key": r.key,
                "status": r.status.value if r.status else "unset",
                "priority": r.priority,
                "reason_code": r.reason_code.value if r.reason_code else None,
                "proof_hash": r.proof_hash,
                "attempts": r.attempts,
                "swept": r.swept,
                "proof": proofs.get(r.key),
            }
        )

    return {"rows": rendered_rows, "tally": tally(rows)}
