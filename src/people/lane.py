"""Title and headline to lanes (P4), on the lane lexicon shared with the job pipeline. Pure.

A lane is assigned when one of its platform names or aliases appears as whole words in the title
or the headline as the source returned them. A person may be in several lanes, in lexicon order.
A lane with no terms (Data and AI until the product owner supplies them) never matches.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from people.config import LaneTerms
from people.cues import clean, find_cue


@dataclass(frozen=True)
class LaneMatch:
    """A lane and the matching substring."""

    lane: str
    evidence: str


def lanes_for(
    title: str | None, headline: str | None, lanes: Sequence[LaneTerms]
) -> tuple[LaneMatch, ...]:
    """Every lane named in the title or headline."""
    texts = (clean(title), clean(headline))
    found: list[LaneMatch] = []
    for lane in lanes:
        for text in texts:
            hit = find_cue(text, lane.terms)
            if hit is not None:
                found.append(LaneMatch(lane.lane_id, hit))
                break
    return tuple(found)
