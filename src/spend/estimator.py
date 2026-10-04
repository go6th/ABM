"""Plan x cost table -> breakdown lines with low, expected and high (pure).

* A provider whose balance snapshot is ``empty``, ``unknown``, missing or stale is excluded from
  the estimate as a flagged line (K15): its operations are not priced and stay unfunded.
* An operation with no cost figure (``known`` False) becomes a bounded **sample** line: at most
  ``sample_size`` calls, each held at ``sample_hold``; the rest of its calls need a new approval
  (K5). Nothing unknown is priced at zero.
* Every amount is integer micro-dollars; sums are exact.

GUARANTEE(test_unknown_cost_is_a_sample_line_never_zero): an unknown operation is never free.
GUARANTEE(test_empty_or_unknown_balance_excludes_the_provider): no estimate on an unknown balance.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Literal

from spend.config import SpendConfig
from spend.models import BalanceSnapshot, BreakdownLine, OpCost, PlanItem


def balance_usable(snap: BalanceSnapshot | None, now: datetime, max_age_minutes: int) -> bool:
    """A snapshot that is ``ok`` or ``low`` and not older than the configured age."""
    if snap is None or snap.status not in ("ok", "low"):
        return False
    return now - snap.taken_at <= timedelta(minutes=max_age_minutes)


def group_plan(plan: Sequence[PlanItem]) -> dict[tuple[str, str], tuple[int, tuple[str, ...]]]:
    """Calls and row keys per (provider code, operation), in first-seen order."""
    out: dict[tuple[str, str], tuple[int, tuple[str, ...]]] = {}
    for item in plan:
        key = (item.provider_code, item.operation)
        calls, rows = out.get(key, (0, ()))
        out[key] = (calls + item.calls, rows if item.row_key in rows else (*rows, item.row_key))
    return out


def item_bounds(
    item: PlanItem, cost: OpCost | None, cfg: SpendConfig
) -> tuple[int, int, int, Literal["priced", "sample"]]:
    """(low, expected, high, status) of one plan item; unknown costs as a bounded sample."""
    if cost is None or not cost.known:
        calls = min(item.calls, cfg.sample_size)
        hold = calls * cfg.sample_hold_micros
        return 0, hold, hold, "sample"
    return (
        item.calls * cost.low_micros,
        item.calls * cost.expected_micros,
        item.calls * cost.high_micros,
        "priced",
    )


def estimate_lines(
    plan: Sequence[PlanItem],
    costs: Mapping[tuple[str, str], OpCost],
    balances: Mapping[str, BalanceSnapshot],
    cfg: SpendConfig,
    now: datetime,
) -> tuple[BreakdownLine, ...]:
    """The advisory breakdown lines (see the module docstring)."""
    lines: list[BreakdownLine] = []
    for (provider, operation), (calls, rows) in group_plan(plan).items():
        category = cfg.category(operation)
        if not balance_usable(balances.get(provider), now, cfg.max_snapshot_age_minutes):
            snap = balances.get(provider)
            why = "no snapshot" if snap is None else f"balance {snap.status}"
            if snap is not None and snap.status in ("ok", "low"):
                why = "balance snapshot stale"
            lines.append(
                BreakdownLine(
                    provider_code=provider,
                    category=category,
                    operation=operation,
                    row_keys=rows,
                    calls=calls,
                    low_micros=0,
                    expected_micros=0,
                    high_micros=0,
                    status="excluded_balance",
                    note=why,
                )
            )
            continue
        cost = costs.get((provider, operation))
        probe = PlanItem(
            row_key=rows[0],
            stage="",
            priority="High",
            provider_code=provider,
            operation=operation,
            calls=calls,
        )
        low, expected, high, status = item_bounds(probe, cost, cfg)
        note = None
        if status == "sample":
            note = (
                f"cost unknown: sample of {min(calls, cfg.sample_size)} calls;"
                " the rest needs a new approval"
            )
        lines.append(
            BreakdownLine(
                provider_code=provider,
                category=category,
                operation=operation,
                row_keys=rows,
                calls=min(calls, cfg.sample_size) if status == "sample" else calls,
                low_micros=low,
                expected_micros=expected,
                high_micros=high,
                status=status,
                note=note,
            )
        )
    return tuple(lines)
