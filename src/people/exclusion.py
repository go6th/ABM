"""Exclusion and review cues on the title (P4). Pure.

Exclusion rules are checked first and beat everything else: a matching title is
``excluded(reason)`` with the rule id and the matching substring. A title that names part of an
exclusion without its qualifier (a review cue) is ``review(exclusion_ambiguous)``. Otherwise the
title passes. No model takes part.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from people.config import CueRule
from people.cues import clean, find_cue


@dataclass(frozen=True)
class ExclusionVerdict:
    """``include``, ``excluded`` or ``review``, with the rule and the matching substring."""

    outcome: Literal["include", "excluded", "review"]
    reason: str | None = None
    rule_id: str | None = None
    evidence: str | None = None


def check_exclusion(
    title: str | None, exclusions: Sequence[CueRule], reviews: Sequence[CueRule]
) -> ExclusionVerdict:
    """The exclusion verdict for one title (exclusion before review)."""
    text = clean(title)
    for rule in exclusions:
        hit = find_cue(text, rule.cues)
        if hit is not None:
            return ExclusionVerdict("excluded", rule.value, rule.rule_id, hit)
    for rule in reviews:
        hit = find_cue(text, rule.cues)
        if hit is not None:
            return ExclusionVerdict("review", rule.value, rule.rule_id, hit)
    return ExclusionVerdict("include")
