"""Priority A, B or C from the title alone (P4). Pure; the first matching rule wins.

The owner's reference rates a person on the title only, never on the level, lane or person:
the ordered ``priority_rules`` (C overrides, C for a non-IT function unless the title names a
platform, B for M&A and business integration, A for platform names and IT leadership), and the
configured default (B) when none matches. A rule's ``unless_cues`` switch it off when the title
names a platform.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from people.config import TitleRule
from people.cues import clean, find_any, find_cue


@dataclass(frozen=True)
class PriorityMatch:
    """The priority, the rule that set it and the matching title substring (empty for the
    default)."""

    priority: str
    rule_id: str
    evidence: str = ""


def priority_for_title(
    title: str | None, rules: Sequence[TitleRule], default: str, default_rule: str
) -> PriorityMatch:
    """The priority of ``title``: the first rule that fires, else the default."""
    text = clean(title)
    for rule in rules:
        hit = find_any(text, rule.cues, rule.patterns)
        if hit is None:
            continue
        if rule.unless_cues and find_cue(text, rule.unless_cues) is not None:
            continue
        return PriorityMatch(rule.value, rule.rule_id, hit)
    return PriorityMatch(default, default_rule)
