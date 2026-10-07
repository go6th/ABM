"""Near-duplicate clustering (DS1, D-73) by fixed rules. Two candidates of the same type and
entity in neighbouring date buckets are compared by title-token Jaccard similarity:

* at or above ``same``: the same event (the second attaches as evidence);
* between ``ambiguous`` and ``same``: ambiguous: linked, delivered in ONE alert that lists both
  sources, never silently merged or dropped;
* below: different events.

Pure. GUARANTEE(test_clusters_merge_same_link_ambiguous_and_keep_distinct)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from signals.fingerprint import normalise

Relation = Literal["same", "ambiguous", "distinct"]


@dataclass(frozen=True)
class Candidate:
    """What clustering compares."""

    event_type: str
    entity: str
    title: str
    bucket: int


def jaccard(a: str, b: str) -> float:
    """Token Jaccard similarity of two normalised titles."""
    ta, tb = set(normalise(a).split()), set(normalise(b).split())
    if not ta and not tb:
        return 1.0
    return len(ta & tb) / len(ta | tb)


def relate(a: Candidate, b: Candidate, *, same: float, ambiguous: float) -> Relation:
    """The fixed-rule relation of two candidates."""
    if not 0 <= ambiguous <= same <= 1:
        raise ValueError("thresholds must satisfy 0 <= ambiguous <= same <= 1")
    if a.event_type != b.event_type or normalise(a.entity) != normalise(b.entity):
        return "distinct"
    if abs(a.bucket - b.bucket) > 1:
        return "distinct"
    sim = jaccard(a.title, b.title)
    if sim >= same:
        return "same"
    if sim >= ambiguous:
        return "ambiguous"
    return "distinct"
