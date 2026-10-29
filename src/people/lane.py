"""Title and headline to lanes (P4), on the lane lexicon shared with the job pipeline. Pure.

A platform lane is assigned when one of its platform names or aliases appears as whole words in
the title or the headline as the source returned them. A person may be in several platform
lanes, in lexicon order; a lane with no terms never matches. The gap rule and the association
search read these lanes.

The People List's own lane is a different thing: ONE role-family lane per person from the title
alone, by the ordered ``people_lanes`` rules (``role_lane_for``): the first lane with a hit wins,
and a title with no hit is the default lane.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from people.config import LaneTerms, TitleRule
from people.cues import clean, find_any, find_cue


@dataclass(frozen=True)
class LaneMatch:
    """A lane and the matching substring."""

    lane: str
    evidence: str


def lanes_for(
    title: str | None, headline: str | None, lanes: Sequence[LaneTerms]
) -> tuple[LaneMatch, ...]:
    """Every platform lane named in the title or headline."""
    texts = (clean(title), clean(headline))
    found: list[LaneMatch] = []
    for lane in lanes:
        for text in texts:
            hit = find_cue(text, lane.terms)
            if hit is not None:
                found.append(LaneMatch(lane.lane_id, hit))
                break
    return tuple(found)


@dataclass(frozen=True)
class RoleLane:
    """The People List lane id, the rule that set it and the matching title substring (empty for
    the default lane)."""

    lane: str
    rule_id: str
    evidence: str


def role_lane_for(
    title: str | None, rules: Sequence[TitleRule], default: str, default_rule: str
) -> RoleLane:
    """The first role-family lane whose cues or patterns match the title, else the default."""
    text = clean(title)
    for rule in rules:
        hit = find_any(text, rule.cues, rule.patterns)
        if hit is not None:
            return RoleLane(rule.value, rule.rule_id, hit)
    return RoleLane(default, default_rule, "")
