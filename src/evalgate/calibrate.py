"""An LLM judge may label support only once its agreement with human labels is calibrated above
the configured floor (E1). Agreement and Cohen's kappa are computed in code.

GUARANTEE(test_an_uncalibrated_judge_never_gates): E1.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Calibration:
    """Judge versus human labels on the same items."""

    n: int
    agreement: float | None
    kappa: float | None
    may_gate: bool


def calibrate(judge: Sequence[bool], human: Sequence[bool], floor: float) -> Calibration:
    """Raw agreement, Cohen's kappa and whether the judge may gate."""
    if len(judge) != len(human):
        raise ValueError("labels must be paired")
    n = len(judge)
    if n == 0:
        return Calibration(0, None, None, False)
    agree = sum(1 for a, b in zip(judge, human, strict=True) if a == b) / n
    pj, ph = sum(judge) / n, sum(human) / n
    expected = pj * ph + (1 - pj) * (1 - ph)
    kappa = None if expected == 1 else (agree - expected) / (1 - expected)
    return Calibration(n, agree, kappa, agree >= floor)
