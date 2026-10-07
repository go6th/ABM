"""The delta record (RF6, D-72): computed by code from append-only facts; the only input to the
Summary diff and the alert engine. Pure.

A fact is identified across runs by its ``identity`` (attribute key plus normalised entity, the
grading engine's fingerprint basis). Comparing the account's current facts before and after a
refresh gives:

* ``new``: an identity present only after;
* ``expired``: an identity whose fact was expired (moved to history) in this refresh;
* ``regraded``: the same identity with a different product grade (``up`` or ``down`` by the
  grade order);
* ``count_change``: a counted attribute (open postings in a lane, people by platform) whose value
  changed, with both values and the base it was counted over.

An empty delta is a value (``no_change``) carrying what was checked and when, never omitted.
The delta score is a documented weighted sum (``config/refresh.yaml`` ``delta_weights``).

GUARANTEE(test_golden_deltas_for_every_change_kind)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

GRADE_ORDER = ("lead", "hypothesis", "probable", "confirmed")  # internal and None are not ordered

ItemKind = Literal["new", "expired", "regraded", "count_change"]


@dataclass(frozen=True)
class FactView:
    """What the delta needs of one current fact."""

    fact_id: str
    identity: str
    signal_type: str
    grade: str | None
    source_code: str | None


@dataclass(frozen=True)
class Count:
    """A counted attribute and the base it was counted over."""

    key: str
    value: int
    base: str


@dataclass(frozen=True)
class DeltaItem:
    """One change, traceable to a fact (``fact_id`` is the fact after the change, or the expired
    fact for ``expired``; count changes name the count key)."""

    kind: ItemKind
    signal_type: str
    identity: str
    fact_id: str | None
    before: str | None = None
    after: str | None = None
    direction: Literal["up", "down"] | None = None
    base: str | None = None
    source_code: str | None = None


@dataclass(frozen=True)
class Delta:
    """A whole delta record."""

    items: tuple[DeltaItem, ...]
    checked: tuple[str, ...]
    checked_at: datetime
    no_change: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "no_change", not self.items)


def _rank(grade: str | None) -> int:
    return GRADE_ORDER.index(grade) if grade in GRADE_ORDER else -1


def compute(
    before: Sequence[FactView],
    after: Sequence[FactView],
    expired: Sequence[FactView],
    counts_before: Mapping[str, Count],
    counts_after: Mapping[str, Count],
    *,
    checked: Sequence[str],
    checked_at: datetime,
) -> Delta:
    """The delta between two current-fact sets (deterministic order)."""
    old = {f.identity: f for f in before}
    new = {f.identity: f for f in after}
    gone = {f.identity for f in expired}
    items: list[DeltaItem] = []
    for ident in sorted(new):
        f = new[ident]
        prev = old.get(ident)
        if prev is None:
            items.append(
                DeltaItem(
                    "new", f.signal_type, ident, f.fact_id, after=f.grade, source_code=f.source_code
                )
            )
        elif prev.grade != f.grade:
            up = _rank(f.grade) > _rank(prev.grade)
            items.append(
                DeltaItem(
                    "regraded",
                    f.signal_type,
                    ident,
                    f.fact_id,
                    before=prev.grade,
                    after=f.grade,
                    direction="up" if up else "down",
                    source_code=f.source_code,
                )
            )
    for f in sorted(expired, key=lambda x: x.identity):
        if f.identity in gone and f.identity not in new:
            items.append(
                DeltaItem(
                    "expired",
                    f.signal_type,
                    f.identity,
                    f.fact_id,
                    before=f.grade,
                    source_code=f.source_code,
                )
            )
    for key in sorted(set(counts_before) | set(counts_after)):
        b, a = counts_before.get(key), counts_after.get(key)
        bv, av = (b.value if b else 0), (a.value if a else 0)
        if bv != av:
            items.append(
                DeltaItem(
                    "count_change",
                    key,
                    key,
                    None,
                    before=str(bv),
                    after=str(av),
                    base=_base(a, b),
                )
            )
    return Delta(tuple(items), tuple(checked), checked_at)


def _base(after: Count | None, before: Count | None) -> str | None:
    """The base a count change is stated against (the newer count's base when there is one)."""
    chosen = after if after is not None else before
    return None if chosen is None else chosen.base


def score(delta: Delta, weights: Mapping[str, Any]) -> int:
    """The delta score: new items weighted by signal type, the rest by kind (integers)."""
    new_w: Mapping[str, Any] = weights["new"]
    total = 0
    for it in delta.items:
        if it.kind == "new":
            total += int(new_w.get(it.signal_type, new_w["other"]))
        elif it.kind == "regraded":
            total += int(weights["regraded_up" if it.direction == "up" else "regraded_down"])
        else:
            total += int(weights[it.kind])
    return total
