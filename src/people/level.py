"""Title to level (P4). Pure.

Each configured level rule has whole-word cues. When cues of several levels match, the most senior
level in ``level_order`` wins (so "Senior Vice President" is ``svp``, not ``vp``, and "Senior
Manager" is ``senior_manager``, not ``manager``). No cue matched means the level is unknown,
never guessed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from people.config import CueRule
from people.cues import clean, find_cue


@dataclass(frozen=True)
class LevelMatch:
    """The level, the rule that set it and the matching title substring."""

    level: str
    rule_id: str
    evidence: str


def classify_level(
    title: str | None, rules: Sequence[CueRule], order: Sequence[str]
) -> LevelMatch | None:
    """The most senior matching level, or ``None`` when no cue matches."""
    text = clean(title)
    rank = {level: i for i, level in enumerate(order)}
    best: LevelMatch | None = None
    for rule in rules:
        hit = find_cue(text, rule.cues)
        if hit is None:
            continue
        if best is None or rank[rule.value] < rank[best.level]:
            best = LevelMatch(rule.value, rule.rule_id, hit)
    return best
