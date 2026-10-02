"""Deterministic classification rules (J4, D-16 proposals). Pure.

Cues match whole words in the description. One class hit: that class (``rule_match``). Hits for
two or more classes: ``ambiguous_rules``. No hit: ``no_rule_match``. Both of the latter go to
the tool-less extractor; the rules never guess.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from jobs.config import Rule
from jobs.match import term_pattern


@dataclass(frozen=True)
class RuleVerdict:
    """The rule engine's answer; ``label`` is ``None`` unless exactly one class hit."""

    label: str | None
    reason: str
    rule_id: str | None = None


def classify_rules(text: str, rules: Sequence[Rule]) -> RuleVerdict:
    """Apply the configured cues to the description text."""
    hits = [r for r in rules if any(term_pattern(c).search(text) for c in r.cues)]
    labels = {r.label for r in hits}
    if len(labels) > 1:
        return RuleVerdict(None, "ambiguous_rules")
    if hits:
        return RuleVerdict(hits[0].label, "rule_match", hits[0].rule_id)
    return RuleVerdict(None, "no_rule_match")
