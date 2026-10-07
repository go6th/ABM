"""Removal debounce (RF5): an item absent once is ``missing_once``; it becomes ``removed`` only
after the configured number of consecutive absent probes; a reappearance cancels the pending
removal; a Blocked or failed source is not an observation of absence. Pure.

GUARANTEE(test_removals_need_consecutive_absences_and_outages_do_not_count)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

State = Literal["present", "missing_once", "missing", "removed"]
Observation = Literal["present", "absent", "blocked"]
Event = Literal["none", "pending_removal", "removal_cancelled", "removed", "reappeared"]


@dataclass(frozen=True)
class Tracked:
    """An item's debounce state."""

    state: State
    absences: int


def observe(prev: Tracked, seen: Observation, *, absences_to_remove: int) -> tuple[Tracked, Event]:
    """The next state and the event it emits."""
    if absences_to_remove < 1:
        raise ValueError("absences_to_remove must be at least 1")
    if seen == "blocked":
        return prev, "none"  # an outage says nothing about the item
    if seen == "present":
        if prev.state == "removed":
            return Tracked("present", 0), "reappeared"
        if prev.absences:
            return Tracked("present", 0), "removal_cancelled"
        return Tracked("present", 0), "none"
    if prev.state == "removed":
        return prev, "none"
    n = prev.absences + 1
    if n >= absences_to_remove:
        return Tracked("removed", n), "removed"
    return Tracked("missing_once" if n == 1 else "missing", n), "pending_removal"
