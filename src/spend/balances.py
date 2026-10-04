"""Free balance preflight and snapshots (K15).

Each provider of the plan is asked once; a failure or a missing adapter gives an ``unknown``
snapshot with the reason as its source (never an inferred balance). Snapshots are stored per run
and phase (start, end) and go stale after ``max_snapshot_age_minutes`` (``spend.estimator``).

GUARANTEE(test_balances_unknown_when_not_configured): no balance is ever guessed.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from spend.errors import BalanceUnknown, NotConfigured
from spend.models import BalanceSnapshot
from spend.ports import BalancePort


async def preflight(
    port: BalancePort | None, providers: Iterable[str], now: datetime
) -> dict[str, BalanceSnapshot]:
    """One snapshot per provider; ``unknown`` with the reason when it cannot be read."""
    out: dict[str, BalanceSnapshot] = {}
    for code in sorted(set(providers)):
        if port is None:
            out[code] = BalanceSnapshot(
                provider_code=code, status="unknown", source="no balance adapter", taken_at=now
            )
            continue
        try:
            out[code] = await port.balance(code)
        except (NotConfigured, BalanceUnknown) as exc:
            out[code] = BalanceSnapshot(
                provider_code=code,
                status="unknown",
                source=f"unavailable: {type(exc).__name__}",
                taken_at=now,
            )
    return out


class UnconfiguredBalances:
    """``BalancePort`` until a provider balance endpoint is sourced ([VERIFY] V-SPEND-2)."""

    async def balance(self, provider_code: str) -> BalanceSnapshot:
        """Always ``NotConfigured``."""
        raise NotConfigured(f"no sourced balance endpoint for {provider_code}")
