"""Exact metric definitions (E3), each with its base. Pure: inputs are typed rows read from the
store; no model is involved (E1).

* coverage: expected attributes found over expected; High tracker rows Done or None over High rows;
* citation validity: cited claims whose URL has a successful stored fetch AND whose sentence is
  present verbatim in the stored text, over cited claims;
* grade accuracy: per-grade precision and recall against analyst labels and the confusion matrix;
* calibration error: weighted mean |accuracy - mean confidence| over equal-width bins;
* unsupported-claim rate: unsupported over all claims;
* cost per run (sum of call costs; unknown cost makes it unknown, never zero) and the p95 run
  duration (nearest-rank).

GUARANTEE(test_metric_known_answers): hand-computed values on small labelled sets.
GUARANTEE(test_metrics_are_invariant_to_case_order): property test.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

GRADES = ("confirmed", "probable", "hypothesis", "lead")


@dataclass(frozen=True)
class Ratio:
    """A rate with its base; ``value`` is None when the base is empty."""

    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        """numerator / denominator, or None on an empty base."""
        return None if self.denominator == 0 else self.numerator / self.denominator


def coverage(expected: Iterable[str], found: Iterable[str]) -> Ratio:
    """Expected attributes found over expected attributes."""
    e = set(expected)
    return Ratio(len(e & set(found)), len(e))


def high_row_coverage(statuses: Mapping[str, str]) -> Ratio:
    """High rows Done or None over High rows (``statuses`` holds High rows only)."""
    return Ratio(sum(1 for s in statuses.values() if s in ("done", "none")), len(statuses))


@dataclass(frozen=True)
class Citation:
    """One cited claim's evidence checks."""

    fetched_ok: bool
    sentence_in_text: bool


def citation_validity(citations: Iterable[Citation]) -> Ratio:
    """Valid citations over cited claims."""
    cs = list(citations)
    return Ratio(sum(1 for c in cs if c.fetched_ok and c.sentence_in_text), len(cs))


@dataclass(frozen=True)
class GradeAccuracy:
    """Per-grade precision and recall and the confusion matrix (label x predicted)."""

    precision: Mapping[str, Ratio]
    recall: Mapping[str, Ratio]
    confusion: Mapping[tuple[str, str], int]


def grade_accuracy(pairs: Iterable[tuple[str, str]]) -> GradeAccuracy:
    """``pairs`` are (predicted, label)."""
    ps = list(pairs)
    confusion: dict[tuple[str, str], int] = {}
    for pred, label in ps:
        confusion[(label, pred)] = confusion.get((label, pred), 0) + 1
    precision = {
        g: Ratio(sum(1 for p, lb in ps if p == g and lb == g), sum(1 for p, _ in ps if p == g))
        for g in GRADES
    }
    recall = {
        g: Ratio(sum(1 for p, lb in ps if p == g and lb == g), sum(1 for _, lb in ps if lb == g))
        for g in GRADES
    }
    return GradeAccuracy(precision, recall, confusion)


def calibration_error(items: Sequence[tuple[float, bool]], bins: int) -> float | None:
    """Weighted mean |accuracy - mean confidence| over equal-width confidence bins (1.0 falls in
    the last bin); None for no items."""
    if not items:
        return None
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        inside = [(c, ok) for c, ok in items if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if not inside:
            continue
        acc = sum(1 for _, ok in inside if ok) / len(inside)
        conf = sum(c for c, _ in inside) / len(inside)
        total += len(inside) / len(items) * abs(acc - conf)
    return total


def unsupported_rate(supported: Iterable[bool]) -> Ratio:
    """Unsupported claims over all claims."""
    s = list(supported)
    return Ratio(sum(1 for x in s if not x), len(s))


def run_cost(costs: Iterable[Decimal | None]) -> Decimal | None:
    """Sum of call costs; None when any is unknown (never treated as zero)."""
    total = Decimal(0)
    for c in costs:
        if c is None:
            return None
        total += c
    return total


def p95(values: Sequence[float]) -> float | None:
    """Nearest-rank 95th percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
