"""Certifications to lanes (P8). Pure.

Only the structured certifications field is read; a certification counts for a lane when one of
the lane's platform names or aliases (the lexicon shared with the job pipeline) appears in it as
whole words. A title or a description is never read for certifications.
"""

from __future__ import annotations

from collections.abc import Sequence

from people.config import LaneTerms
from people.cues import clean, find_cue


def certified_lanes(certifications: Sequence[str], lanes: Sequence[LaneTerms]) -> tuple[str, ...]:
    """The lanes named by at least one certification, in lexicon order."""
    texts = [clean(c, 300) for c in certifications]
    return tuple(
        lane.lane_id
        for lane in lanes
        if lane.terms and any(find_cue(t, lane.terms) is not None for t in texts)
    )
