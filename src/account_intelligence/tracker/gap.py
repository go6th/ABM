"""Gap-loop helpers (section 7). Pure.

``open_rows`` lists rows still worth another pass (unset, Not done, Blocked, Partial), High
first, then checklist order, excluding exhausted rows (attempts reached the cap of 3).
``gap_pass_result`` reports progress when any row moved up the ladder or kept its status with
strictly more calls in its proof. A changed hash alone is not progress (audit F-12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from account_intelligence.tracker.completion import RowState
from account_intelligence.tracker.config import load_tracker_config
from account_intelligence.tracker.model import Status
from account_intelligence.tracker.tally import sort_for_rendering


class ExitReason(StrEnum):
    """Loop exit reasons shared with the workflow (written into the run state)."""

    CONVERGED = "converged"
    CAP_REACHED = "cap_reached"
    NO_PROGRESS = "no_progress"
    BUDGET = "budget"
    DEADLINE = "deadline"


@dataclass
class TrackerRowState(RowState):
    """A row with its attempt count (the gap loop's input)."""

    attempts: int = field(default=0, kw_only=True)


_OPEN = (None, Status.NOT_DONE, Status.BLOCKED, Status.PARTIAL)


def open_rows(rows: list[TrackerRowState], cap: int | None = None) -> list[TrackerRowState]:
    """Rows to retry, most valuable first; exhausted rows are excluded."""
    limit = load_tracker_config().attempts_per_row if cap is None else cap
    return list(sort_for_rendering([r for r in rows if r.attempts < limit and r.status in _OPEN]))


def _score(status: Status | None) -> int:
    if status in (Status.DONE, Status.NONE):
        return 3
    if status is Status.PARTIAL:
        return 2
    if status in (Status.NOT_DONE, Status.BLOCKED):
        return 1
    return 0


def gap_pass_result(before: list[RowState], after: list[RowState]) -> bool:
    """True when the pass made progress (a row moved up, or kept its status with more calls)."""
    earlier = {r.key: r for r in before}
    for row in after:
        prior = earlier.get(row.key)
        if prior is None:
            continue
        if _score(row.status) > _score(prior.status):
            return True
        if row.status == prior.status and row.proof_calls > prior.proof_calls:
            return True
    return False


def all_high_converged(rows: list[RowState]) -> bool:
    """The ``converged`` exit: every High row is Done or None."""
    return all(r.status in (Status.DONE, Status.NONE) for r in rows if r.priority == "high")
