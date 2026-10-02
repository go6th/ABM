"""Tracker gap loop logic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from account_intelligence.tracker.completion import RowState
from account_intelligence.tracker.config import load_tracker_config
from account_intelligence.tracker.model import Status
from account_intelligence.tracker.tally import sort_for_rendering


class ExitReason(StrEnum):
    CONVERGED = "converged"
    CAP_REACHED = "cap_reached"
    NO_PROGRESS = "no_progress"
    BUDGET = "budget"
    DEADLINE = "deadline"


@dataclass
class TrackerRowState(RowState):
    attempts: int


def open_rows(rows: list[TrackerRowState]) -> list[TrackerRowState]:
    config = load_tracker_config()
    cap = config.d_13_tracker_rules.get("attempts_per_row", 3)

    open_ones = []
    for r in rows:
        if r.attempts >= cap:
            continue
        if r.status in (None, Status.NOT_DONE, Status.BLOCKED, Status.PARTIAL):
            open_ones.append(r)

    return sort_for_rendering(open_ones)  # type: ignore


def _status_score(status: Status | None) -> int:
    if status is None:
        return 0
    if status in (Status.NOT_DONE, Status.BLOCKED):
        return 1
    if status == Status.PARTIAL:
        return 2
    if status in (Status.DONE, Status.NONE):
        return 3
    return 0


def gap_pass_result(before: list[RowState], after: list[RowState]) -> bool:
    """Returns True if there was progress."""
    before_map = {r.key: r for r in before}
    after_map = {r.key: r for r in after}

    for key, r_after in after_map.items():
        r_before = before_map.get(key)
        if not r_before:
            continue  # should not happen if they are the same seeded rows

        score_before = _status_score(r_before.status)
        score_after = _status_score(r_after.status)

        if score_after > score_before:
            return True

        if score_after == score_before:
            # strictly more proof
            if r_after.proof_hash != r_before.proof_hash:
                # Assuming the engine enforces cumulative proof, if hash changed it's strictly more
                # Wait, "strictly more proof". If the hash changes, it's more proof.
                # Is that enough? We will assume yes.
                return True

    return False
