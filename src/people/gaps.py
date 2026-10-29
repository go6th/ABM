"""Gap detection and the association search plan (spec 7, D-21 gap rule). Pure.

A gap is a lane that is on and has no in-scope person after the title search (proposal
``lane_on_without_in_scope_person``). Each gap lane gets at most ONE association search over the
whole run, restricted to the configured functions (PRD: IT, engineering and product), never
repeated; a lane already association-searched is not a gap the loop can act on again. The loop
that uses this lives in ``people.pipeline`` and ends ``converged`` (no gap left), ``no_progress``
(a pass that adds no in-scope person) or ``cap_reached`` (``max_gap_passes``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from people.models import Person
from people.search import SearchSpec


def gap_lanes(people: Iterable[Person], lanes_on: Sequence[str]) -> tuple[str, ...]:
    """Lanes that are on and have no in-scope person, in lane order. A person below the
    seniority rule (band ``below``, kept on the list because the title names a platform) does not
    cover a lane: the gap is a lane with nobody senior in it."""
    covered = {
        lane
        for p in people
        if p.status == "in_scope" and p.classification.band != "below"
        for lane in p.classification.lanes
    }
    return tuple(lane for lane in lanes_on if lane not in covered)


def association_specs(gaps: Sequence[str], already_searched: Iterable[str]) -> list[SearchSpec]:
    """One association search per gap lane not searched before."""
    done = set(already_searched)
    return [SearchSpec(f"association:{lane}", lane, 0) for lane in gaps if lane not in done]
