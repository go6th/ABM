from collections.abc import Sequence

"""Tracker completion evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from account_intelligence.tracker.model import ReasonCode, Status


@dataclass
class RowState:
    key: str
    priority: str
    status: Status | None
    reason_code: ReasonCode | None
    proof_hash: str | None
    swept: bool


@dataclass
class CompletionVerdict:
    level: str  # complete, complete_with_gaps, cannot_complete
    can_complete: bool
    blockers: list[dict[str, Any]]
    tally: dict[str, int]
    completeness_ratio: float
    completeness_base: int


def evaluate(rows: Sequence[RowState], policy: dict[str, Any]) -> CompletionVerdict:
    blockers = []
    tally = {"done": 0, "partial": 0, "not_done": 0, "blocked": 0, "none": 0, "unset": 0}
    high_rows = 0
    high_done_or_none = 0

    for r in rows:
        if r.status:
            tally[r.status.value] += 1
        else:
            tally["unset"] += 1

        if r.priority == "high":
            high_rows += 1
            if r.status in (Status.DONE, Status.NONE):
                high_done_or_none += 1

        # Hard blocks
        if r.status is None:
            blockers.append({"key": r.key, "status": "unset", "reason": None})
            continue

        if r.swept:
            blockers.append(
                {
                    "key": r.key,
                    "status": r.status.value,
                    "reason": r.reason_code.value if r.reason_code else None,
                }
            )
            continue

        if r.status is not None and not r.proof_hash:
            blockers.append({"key": r.key, "status": r.status.value, "reason": "missing_proof"})
            continue

        # Gaps (High row rules based on policy)
        if r.priority == "high":
            allowed = policy.get("high_rows_allowed_for_completed", ["done", "none"])
            if r.status.value not in allowed:
                blockers.append(
                    {
                        "key": r.key,
                        "status": r.status.value,
                        "reason": r.reason_code.value if r.reason_code else None,
                    }
                )

    completeness_ratio = (high_done_or_none / high_rows) if high_rows > 0 else 1.0

    can_complete = len(blockers) == 0

    # "any row is unset; any row is swept; any row has a status without valid proof."
    # The policy controls whether a partial/blocked high row blocks.
    # What if it's partial but policy allows it? Then it's not a blocker.
    # The prompt says: "Verdict fields: level (complete, complete_with_gaps, cannot_complete)"

    level = "cannot_complete"
    if can_complete:
        # Check if there are any gaps (open rows) that aren't high priority, or if high rows have gaps allowed by policy
        has_gaps = any(
            r.status in (Status.PARTIAL, Status.NOT_DONE, Status.BLOCKED)
            for r in rows
            if r.status is not None
        )
        level = "complete_with_gaps" if has_gaps else "complete"

    return CompletionVerdict(
        level=level,
        can_complete=can_complete,
        blockers=blockers,
        tally=tally,
        completeness_ratio=completeness_ratio,
        completeness_base=high_rows,
    )
