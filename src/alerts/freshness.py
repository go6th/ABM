"""Alert freshness SLI (AL6): the share of alerts delivered within 24 h of the signal
(SLO 95%, sourced), from ``signal_detected_at`` and ``delivered_at``. Undelivered alerts older
than the window count as misses; younger ones are not yet judged. Pure.

GUARANTEE(test_the_freshness_sli_counts_misses_and_pending)
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

WINDOW = timedelta(hours=24)
SLO = 0.95


@dataclass(frozen=True)
class Freshness:
    """The SLI with its counts."""

    good: int
    judged: int
    pending: int

    @property
    def ratio(self) -> float | None:
        """good / judged, or None with nothing judged."""
        return None if self.judged == 0 else self.good / self.judged

    @property
    def meets_slo(self) -> bool | None:
        """Against the 95% SLO (None with nothing judged)."""
        r = self.ratio
        return None if r is None else r >= SLO


def compute(rows: Iterable[tuple[datetime, datetime | None]], now: datetime) -> Freshness:
    """From (signal_detected_at, delivered_at) pairs."""
    good = judged = pending = 0
    for detected, delivered in rows:
        if delivered is not None:
            judged += 1
            good += delivered - detected <= WINDOW
        elif now - detected > WINDOW:
            judged += 1
        else:
            pending += 1
    return Freshness(good, judged, pending)
