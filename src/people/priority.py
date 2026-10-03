"""Priority A, B or C from the configured rubric (P4). Pure; first matching rule wins.

``None`` means no rule matched (the caller sends the person to review); a priority is never
guessed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from people.config import PriorityRule


@dataclass(frozen=True)
class PriorityMatch:
    """The priority and the rule that set it."""

    priority: str
    rule_id: str


def assign_priority(
    band: str, has_lane: bool, rules: Sequence[PriorityRule]
) -> PriorityMatch | None:
    """The first rule whose band and lane requirement match."""
    for rule in rules:
        if rule.band == band and rule.lane_required == has_lane:
            return PriorityMatch(rule.priority, rule.rule_id)
    return None
