"""SLIs, burn rates and error budgets (pure; O8, O9, D-58).

* ``burn(bad, total, target)``: (bad / total) / (1 - target) as an exact ``Fraction``; ``None``
  (NoData) when total is zero; never 100% good by default.
* ``multiwindow_page``: a page needs the long window AND its short window above the factor, and at
  least ``min_events`` in the long window (one failed request never pages).
* ``run_completion_good``: COMPLETED or PARTIAL within the p95 target, human wait excluded,
  CANCELED excluded from the total.
* ``latency_good_from_buckets``: requests under the threshold estimated from histogram buckets;
  the estimate's error is bounded by the bucket that straddles the threshold (returned).

GUARANTEE(test_burn_math_is_exact_and_monotone): property test (O9).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction


def burn(bad: int, total: int, target: Fraction) -> Fraction | None:
    """Burn rate, or None (NoData) when nothing happened."""
    if total < 0 or bad < 0 or bad > total:
        raise ValueError("0 <= bad <= total")
    if total == 0:
        return None
    if target >= 1:
        return Fraction(0) if bad == 0 else Fraction(10**9)  # any bad event exhausts a 100% SLO
    return Fraction(bad, total) / (1 - target)


@dataclass(frozen=True)
class Window:
    """Counts in one window."""

    bad: int
    total: int


def multiwindow_page(
    long: Window, short: Window, target: Fraction, factor: Fraction, min_events: int
) -> bool:
    """See the module docstring."""
    if long.total < min_events:
        return False
    b_long, b_short = burn(long.bad, long.total, target), burn(short.bad, short.total, target)
    return b_long is not None and b_short is not None and b_long > factor and b_short > factor


@dataclass(frozen=True)
class RunOutcome:
    """A terminal run for the completion SLI."""

    status: str
    duration_s: float
    human_wait_s: float


def run_completion(runs: Sequence[RunOutcome], p95_target_s: float) -> tuple[int, int]:
    """(good, total) for the completion SLO."""
    total = [r for r in runs if r.status != "CANCELED"]
    good = [
        r
        for r in total
        if r.status in ("COMPLETED", "PARTIAL") and r.duration_s - r.human_wait_s <= p95_target_s
    ]
    return len(good), len(total)


def latency_good_from_buckets(
    bounds_ms: Sequence[float], counts: Sequence[int], threshold_ms: float
) -> tuple[int, int, int]:
    """(good lower bound, total, error bound): counts per bucket ``(bounds[i-1], bounds[i]]``
    with a final overflow bucket; the straddling bucket's count is the error bound."""
    if len(counts) != len(bounds_ms) + 1:
        raise ValueError("one more count than bounds (the overflow bucket)")
    good = err = 0
    lower = float("-inf")
    for i, upper in enumerate(bounds_ms):
        if upper <= threshold_ms:
            good += counts[i]
        elif lower < threshold_ms:
            err += counts[i]
        lower = upper
    return good, sum(counts), err


def budget_remaining(bad: int, total: int, target: Fraction) -> Fraction | None:
    """Fraction of the error budget left (can go negative); None without data."""
    if total == 0:
        return None
    allowed = (1 - target) * total
    if allowed == 0:
        return Fraction(1) if bad == 0 else Fraction(-bad)
    return 1 - Fraction(bad) / allowed
