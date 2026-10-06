"""Statistics for paired comparisons (D-50, E4, E6). Seeded randomness only (no wall clock).

* ``wilson``: the Wilson score interval of k successes in n trials at confidence 1 - alpha;
* ``paired_bootstrap``: the one-sided p-value that the candidate's mean per-case score is lower
  than the baseline's (resampling cases with replacement, fixed seed); a drop of zero gives 1.0;
* ``holm``: the Holm step-down correction; returns the hypotheses rejected at family-wise alpha.

GUARANTEE(test_statistics_against_reference_values): known answers.
GUARANTEE(test_statistics_stay_inside_their_bounds): property test.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from statistics import NormalDist


def wilson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float] | None:
    """The interval, or None for n == 0."""
    if n == 0:
        return None
    if not 0 <= k <= n:
        raise ValueError("k must be between 0 and n")
    z = NormalDist().inv_cdf(1 - alpha / 2)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    # the exact endpoints at the boundaries (floating error must not exclude p itself)
    lo = 0.0 if k == 0 else min(p, max(0.0, centre - half))
    hi = 1.0 if k == n else max(p, min(1.0, centre + half))
    return lo, hi


def paired_bootstrap(
    baseline: Sequence[float], candidate: Sequence[float], resamples: int, seed: int
) -> float:
    """One-sided p-value for "candidate is worse than baseline" on paired cases."""
    if len(baseline) != len(candidate):
        raise ValueError("paired samples must have the same length")
    n = len(baseline)
    if n == 0:
        return 1.0
    diffs = [c - b for b, c in zip(baseline, candidate, strict=True)]
    observed = sum(diffs) / n
    if observed >= 0:
        return 1.0
    rng = random.Random(seed)
    centred = [d - observed for d in diffs]  # the null: no difference
    extreme = 0
    for _ in range(resamples):
        sample = sum(centred[rng.randrange(n)] for _ in range(n)) / n
        if sample <= observed:
            extreme += 1
    return (extreme + 1) / (resamples + 1)


def holm(pvalues: Mapping[str, float], alpha: float) -> frozenset[str]:
    """Hypotheses rejected by Holm's step-down procedure."""
    ordered = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    m = len(ordered)
    rejected: set[str] = set()
    for i, (name, p) in enumerate(ordered):
        if p <= alpha / (m - i):
            rejected.add(name)
        else:
            break
    return frozenset(rejected)
