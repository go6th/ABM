"""Deterministic classification rules (J4, D-16 proposals). Pure.

Cues match whole words. One class hit: that class (``rule_match``). Hits for two or more classes:
``ambiguous_rules``. No hit: ``no_rule_match``.

Since 2026-10-29 the rules never decide a class. A keyword in a description (an "engineer" in
company boilerplate, a "representative" in a tool list) is not a reading, and it flipped
postings to Build role or User role that nobody had confirmed. The rules are applied to the
TITLE only, as triage (``title_candidate``): a title that carries a Build or Admin and owner cue
is a candidate for the tool-less extractor, the only reader that may confirm those two classes.
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


# the classes only a reading may confirm: a title that cues one of them goes to the extractor
READING_CLASSES = frozenset({"build", "admin_owner"})


def classify_rules(text: str, rules: Sequence[Rule]) -> RuleVerdict:
    """Apply the configured cues to the description text."""
    hits = [r for r in rules if any(term_pattern(c).search(text) for c in r.cues)]
    labels = {r.label for r in hits}
    if len(labels) > 1:
        return RuleVerdict(None, "ambiguous_rules")
    if hits:
        return RuleVerdict(hits[0].label, "rule_match", hits[0].rule_id)
    return RuleVerdict(None, "no_rule_match")


def title_candidate(title: str, rules: Sequence[Rule]) -> bool:
    """True when the TITLE carries a cue of a class only a reading may confirm (Build, Admin and
    owner): the posting is a candidate for the extractor even when its budget is limited. The
    verdict is a hint about who to read first, never a class."""
    return any(
        r.label in READING_CLASSES and any(term_pattern(c).search(title) for c in r.cues)
        for r in rules
    )
