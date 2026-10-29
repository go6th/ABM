"""Title to level (P4). Pure.

Each configured level rule has whole-word cues. When cues of several levels match, a cue whose
match sits inside a longer cue's match is set aside (the longest cue wins, so "associate
director" is ``associate_director`` and not ``director``, "senior vice president" is ``svp`` and
not ``vp``, "senior manager" is ``senior_manager`` and not ``manager``); of the matches that are
left, the most senior level in ``level_order`` wins. No cue matched means the level is unknown:
``classify_level`` returns ``None`` and the caller applies the configured default level (a title
with no level cue is "Lead, owner, specialist or developer"), never a guess at a senior one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from jobs.match import term_pattern
from people.config import CueRule
from people.cues import clean


@dataclass(frozen=True)
class LevelMatch:
    """The level, the rule that set it and the matching title substring."""

    level: str
    rule_id: str
    evidence: str


def classify_level(
    title: str | None, rules: Sequence[CueRule], order: Sequence[str]
) -> LevelMatch | None:
    """The most senior matching level after the longest-cue rule, or ``None`` with no cue."""
    text = clean(title)
    rank = {level: i for i, level in enumerate(order)}
    # (start, end, rule) for the longest match of each rule's cues
    hits: list[tuple[int, int, CueRule]] = []
    for rule in rules:
        best: tuple[int, int] | None = None
        for cue in rule.cues:
            match = term_pattern(cue).search(text)
            if match is not None and (
                best is None or match.end() - match.start() > best[1] - best[0]
            ):
                best = (match.start(), match.end())
        if best is not None:
            hits.append((best[0], best[1], rule))
    # set aside a hit that sits inside a strictly longer hit (a cue within a longer cue)
    kept = [
        (s, e, r)
        for s, e, r in hits
        if not any(s2 <= s and e <= e2 and (e2 - s2) > (e - s) for s2, e2, _ in hits)
    ]
    chosen: LevelMatch | None = None
    for s, e, rule in kept:
        if chosen is None or rank[rule.value] < rank[chosen.level]:
            chosen = LevelMatch(rule.value, rule.rule_id, text[s:e])
    return chosen
