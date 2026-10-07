"""Cost per monitored account and the refresh-to-full-run ratio (CG3), from measured rows only.

* ``per_account``: monitor units by account, monitor kind and month, from ``monitor_runs``
  (operator plane).
* ``refresh_ratio``: mean measured cost of refresh runs over mean measured cost of full runs,
  from ``tool_calls.cost_usd`` (micro-USD, exact integers), with its base and as-of; below the
  minimum base on either side it says "not enough data" instead of a number. ARCH figures are
  illustrative and never used here.

GUARANTEE(test_cost_reports_use_measured_rows_and_say_not_enough_data)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

MIN_BASE = 10  # proposal: runs needed on each side before a ratio is stated


@dataclass(frozen=True)
class Ratio:
    """The ratio report."""

    refresh_runs: int
    full_runs: int
    refresh_mean_micros: int | None
    full_mean_micros: int | None
    ratio: float | None
    statement: str
    as_of: datetime


def ratio_of(
    refresh_costs: Sequence[int],
    full_costs: Sequence[int],
    as_of: datetime,
    *,
    min_base: int = MIN_BASE,
) -> Ratio:
    """The ratio from per-run costs in micro-USD (pure)."""
    r_n, f_n = len(refresh_costs), len(full_costs)
    r_mean = sum(refresh_costs) // r_n if r_n else None
    f_mean = sum(full_costs) // f_n if f_n else None
    if r_n < min_base or f_n < min_base or not f_mean:
        return Ratio(
            r_n,
            f_n,
            r_mean,
            f_mean,
            None,
            f"not enough data ({r_n} refreshes, {f_n} full runs; {min_base} each needed)",
            as_of,
        )
    assert r_mean is not None
    ratio = round(r_mean / f_mean, 4)
    return Ratio(
        r_n,
        f_n,
        r_mean,
        f_mean,
        ratio,
        f"a refresh cost {ratio:.2%} of a full run (base {r_n} refreshes, {f_n} full runs)",
        as_of,
    )


async def per_account(s: Any) -> list[dict[str, Any]]:
    """Monitor units by account, kind and month (the context tenant)."""
    rows = (
        await s.execute(
            text(
                "SELECT account_id, kind, to_char(at, 'YYYY-MM') AS month, sum(units) AS units,"
                " count(*) AS runs FROM monitor_runs GROUP BY account_id, kind, month"
                " ORDER BY month, account_id, kind"
            )
        )
    ).all()
    return [
        {
            "account_id": str(r.account_id),
            "kind": r.kind,
            "month": r.month,
            "units": int(r.units),
            "runs": int(r.runs),
        }
        for r in rows
    ]


async def refresh_ratio(s: Any, as_of: datetime) -> Ratio:
    """The measured ratio for the context tenant."""
    rows = (
        await s.execute(
            text(
                "SELECT r.id, (r.id IN (SELECT run_id FROM refresh_runs WHERE run_id IS NOT NULL))"
                " AS is_refresh, coalesce(sum(t.cost_usd), 0) AS usd FROM account_runs r"
                " LEFT JOIN tool_calls t ON t.run_id = r.id WHERE r.status IN ('COMPLETED',"
                " 'PARTIAL')"
                " GROUP BY r.id"
            )
        )
    ).all()
    micros = [(bool(r.is_refresh), int(Decimal(r.usd) * 1_000_000)) for r in rows]
    return ratio_of([m for f, m in micros if f], [m for f, m in micros if not f], as_of)
