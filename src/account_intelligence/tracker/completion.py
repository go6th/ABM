"""``evaluate``: the completion verdict (section 5.5, policy proposal D-12). Pure.

Hard blocks, never overridden by policy: no seeded tracker, fewer rows than seeded, any unset
row, any swept row, any status without proof. Gaps: a High row whose status is not in the pinned
policy's ``high_rows_allowed_for_completed`` (default ``[done, none]``, the PRD quality metric),
unless its reason is in ``high_reasons_allowed_for_completed`` (input mode "file": the row is a
stated gap of the uploaded material, never an unexplained one).

GUARANTEE(test_file_mode_gaps_with_reasons_may_complete)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from account_intelligence.tracker.model import ReasonCode, Status

DEFAULT_ALLOWED_HIGH = ("done", "none")


@dataclass
class RowState:
    """A row as the pure logic sees it. ``proof_calls`` is the number of calls in its proof."""

    key: str
    priority: str
    status: Status | None
    reason_code: ReasonCode | None
    proof_hash: str | None
    swept: bool
    proof_calls: int = field(default=0, kw_only=True)


@dataclass
class CompletionVerdict:
    """Whether the run may be COMPLETED, and why not."""

    level: str  # complete, complete_with_gaps, cannot_complete
    can_complete: bool
    blockers: list[dict[str, Any]]
    tally: dict[str, int]
    completeness_ratio: float
    completeness_base: int

    @property
    def completeness_text(self) -> str:
        """The ratio with its base, e.g. ``15 of 19 High rows Done or None``."""
        done = round(self.completeness_ratio * self.completeness_base)
        return f"{done} of {self.completeness_base} High rows Done or None"


def evaluate(
    rows: Sequence[RowState], policy: dict[str, Any], seeded_count: int | None = -1
) -> CompletionVerdict:
    """Compute the verdict. ``seeded_count`` is the pinned seeded row count; ``None`` means the
    run has no seeded tracker; the default ``-1`` skips the count check (pure-logic callers)."""
    blockers: list[dict[str, Any]] = []
    tally = {"done": 0, "partial": 0, "not_done": 0, "blocked": 0, "none": 0, "unset": 0}
    allowed = tuple(policy.get("high_rows_allowed_for_completed", DEFAULT_ALLOWED_HIGH))
    allowed_reasons = frozenset(policy.get("high_reasons_allowed_for_completed") or ())
    high_rows = high_ok = 0

    if seeded_count is None:
        blockers.append({"key": None, "status": "unseeded", "reason": "run_not_seeded"})
    elif seeded_count >= 0 and len(rows) != seeded_count:
        blockers.append({"key": None, "status": "short", "reason": "row_count_mismatch"})

    for r in rows:
        tally[r.status.value if r.status else "unset"] += 1
        if r.priority == "high":
            high_rows += 1
            high_ok += r.status in (Status.DONE, Status.NONE)
        reason = r.reason_code.value if r.reason_code else None
        if r.status is None:
            blockers.append({"key": r.key, "status": "unset", "reason": None})
        elif r.swept:
            blockers.append({"key": r.key, "status": r.status.value, "reason": reason})
        elif not r.proof_hash:
            blockers.append({"key": r.key, "status": r.status.value, "reason": "missing_proof"})
        elif (
            r.priority == "high"
            and r.status.value not in allowed
            and (reason is None or reason not in allowed_reasons)
        ):
            blockers.append({"key": r.key, "status": r.status.value, "reason": reason})

    can_complete = not blockers
    level = "cannot_complete"
    if can_complete:
        gaps = any(r.status in (Status.PARTIAL, Status.NOT_DONE, Status.BLOCKED) for r in rows)
        level = "complete_with_gaps" if gaps else "complete"
    return CompletionVerdict(
        level=level,
        can_complete=can_complete,
        blockers=blockers,
        tally=tally,
        completeness_ratio=(high_ok / high_rows) if high_rows else 1.0,
        completeness_base=high_rows,
    )
