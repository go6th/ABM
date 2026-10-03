"""Atomic in-graph budget gate on ``account_runs`` (dollars and call count).

``reserve`` is one conditional UPDATE, so concurrent reservations can never take the
run past its dollar cap or its call cap: the row lock serialises them and the WHERE
clause re-checks after the lock. No row means ``BudgetExceeded``; the run then ends
BUDGET_EXCEEDED with partial results. ``true_up`` applies actual minus reserved.
Credits never enter these columns (known defect 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.connectors.errors import BudgetExceeded, ConnectorError


@dataclass(frozen=True)
class BudgetDefaults:
    """Skill defaults for a run's broker budget ($5.00) and call cap (60)."""

    run_budget_usd: float
    tool_call_cap: int


@dataclass(frozen=True)
class Reservation:
    """The run's spend and call count after a successful atomic reservation."""

    spent_usd: Decimal
    tool_calls_used: int


class RunNotFound(ConnectorError):
    """The run is not visible to this tenant (RLS) or does not exist."""

    reason = "run not found for this tenant"


def _money(value: float) -> Decimal:
    return Decimal(str(round(value, 6)))


async def reserve(
    session: AsyncSession, run_id: UUID, estimate_usd: float, defaults: BudgetDefaults
) -> Reservation:
    """Atomically reserve an estimate against the run's budget and call cap; BudgetExceeded
    otherwise."""
    if estimate_usd < 0:
        raise ValueError("estimate must not be negative")
    row = (
        await session.execute(
            text(
                "UPDATE account_runs SET spent_usd = spent_usd + :est, "
                "tool_calls_used = tool_calls_used + 1 "
                "WHERE id = :run "
                "AND spent_usd + :est <= COALESCE(budget_usd, :default_budget) "
                "AND tool_calls_used < COALESCE(tool_call_cap, :default_cap) "
                "RETURNING spent_usd, tool_calls_used"
            ),
            {
                "est": _money(estimate_usd),
                "run": run_id,
                "default_budget": _money(defaults.run_budget_usd),
                "default_cap": defaults.tool_call_cap,
            },
        )
    ).first()
    if row is not None:
        return Reservation(spent_usd=row.spent_usd, tool_calls_used=row.tool_calls_used)
    exists = (
        await session.execute(text("SELECT 1 FROM account_runs WHERE id = :run"), {"run": run_id})
    ).first()
    if exists is None:
        raise RunNotFound()
    raise BudgetExceeded()


async def true_up(session: AsyncSession, run_id: UUID, delta_usd: float) -> None:
    """Apply (actual charge - reservation). Negative refunds the unused part."""
    if delta_usd == 0:
        return
    await session.execute(
        text("UPDATE account_runs SET spent_usd = spent_usd + :d WHERE id = :run"),
        {"d": _money(delta_usd), "run": run_id},
    )
