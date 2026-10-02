"""Tracker views. The client view is allow-list based (T10); the operator view is admin only.

Client rows carry exactly: label, status, priority, client reason text, tool categories (the
client-facing tracker says "tool", never a broker) and the result count. Never: provider codes,
credit or cost figures, call references, queries, internal detail, or names. The rendered view is
run through the client-output scanner before it is returned; a hit raises ``ClientOutputLeak``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from account_intelligence.safety.client_output import assert_client_safe
from account_intelligence.tracker.config import load_checklist, load_tracker_config
from account_intelligence.tracker.gap import TrackerRowState
from account_intelligence.tracker.tally import sort_for_rendering, tally

STATUS_TEXT = {
    "done": "Done",
    "partial": "Partial",
    "not_done": "Not done",
    "blocked": "Blocked",
    "none": "None",
    "unset": "Not yet reported",
}


def _calls(proofs: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    proof = (proofs.get(key) or {}).get("proof") or {}
    return list(proof.get("calls") or [])


def render_client_view(
    rows: Sequence[TrackerRowState], proofs: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Client-safe tracker: explicit field mapping, open work first, tally with bases."""
    config = load_tracker_config()
    labels = {r.key: r.label for r in load_checklist().rows}
    out = []
    for r in sort_for_rendering(rows):
        calls = _calls(proofs or {}, r.key)
        counted = [c["result_count"] for c in calls if c.get("success")]
        out.append(
            {
                "row": labels.get(r.key, r.key),
                "status": STATUS_TEXT[r.status.value if r.status else "unset"],
                "priority": r.priority.capitalize(),
                "reason": config.reason_codes.get(r.reason_code.value) if r.reason_code else None,
                "tools": sorted({str(c["tool_category"]) for c in calls}),
                "results": None if not counted or any(n is None for n in counted) else sum(counted),
            }
        )
    summary = tally(rows)
    view = {"rows": out, "tally": summary["sentences"]}
    assert_client_safe([str(view)])
    return view


def render_operator_view(
    rows: Sequence[TrackerRowState], proofs: Mapping[str, Any]
) -> dict[str, Any]:
    """Operator view (admin role only): adds proof, references, attempts and internal detail."""
    rendered = [
        {
            "key": r.key,
            "status": r.status.value if r.status else "unset",
            "priority": r.priority,
            "reason_code": r.reason_code.value if r.reason_code else None,
            "proof_hash": r.proof_hash,
            "proof_calls": r.proof_calls,
            "attempts": r.attempts,
            "swept": r.swept,
            "proof": (proofs.get(r.key) or {}).get("proof"),
            "detail": (proofs.get(r.key) or {}).get("detail"),
        }
        for r in sort_for_rendering(rows)
    ]
    return {"rows": rendered, "tally": tally(rows)}
