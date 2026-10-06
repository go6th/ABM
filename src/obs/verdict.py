"""Release verdict (O16): candidate versus baseline SLIs for a canary release (pure).
``regression`` when a candidate's bad ratio exceeds the baseline's by more than the tolerance
with enough samples; ``low_sample`` and ``no_baseline`` are explicit outcomes, never a pass.

GUARANTEE(test_release_verdict_outcomes): O16.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Counts:
    """bad / total for one version."""

    bad: int
    total: int


def verdict(candidate: Counts, baseline: Counts | None, min_total: int, tolerance: float) -> str:
    """``promote``, ``regression``, ``low_sample`` or ``no_baseline``."""
    if baseline is None or baseline.total == 0:
        return "no_baseline"
    if candidate.total < min_total or baseline.total < min_total:
        return "low_sample"
    if candidate.bad / candidate.total > baseline.bad / baseline.total + tolerance:
        return "regression"
    return "promote"
