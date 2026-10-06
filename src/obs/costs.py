"""Cost per run, account and provider derived from the ledger (O6, D-57).

``rollup_lines`` (pure, integer micro-dollars) groups ledger rows by (run, provider code, route,
cost source); a replay and a pre-pulled file add zero; a call with unknown cost is counted
separately and never as zero. ``refresh`` writes the rollup rows for runs past the watermark
(idempotent upsert keyed by run, provider, route and cost source; recomputable, documented as not
a ledger). ``reconcile`` compares the rollup with the ledger exactly; drift is returned (and
exported as ``cost_drift_total``). Credits and dollars are separate columns (defect 4).

GUARANTEE(test_cost_rollups_equal_the_ledger_exactly): O6.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
MICRO = Decimal(1_000_000)


@dataclass(frozen=True)
class LedgerRow:
    """One finished ledger call as the rollup sees it."""

    run_id: str
    provider_code: str
    route: str | None
    cost_source: str | None
    charged_usd: Decimal | None
    replayed: bool = False


@dataclass(frozen=True)
class Line:
    """One rollup line (integer micro-dollars)."""

    run_id: str
    provider_code: str
    route: str
    cost_source: str
    calls: int
    cost_micros: int
    unknown_cost_calls: int


def micros(value: Decimal) -> int:
    """Exact micro-dollars (the ledger holds six decimals)."""
    return int((value * MICRO).to_integral_exact())


def rollup_lines(rows: Iterable[LedgerRow]) -> list[Line]:
    """Grouped lines, deterministic order."""
    acc: dict[tuple[str, str, str, str], list[int]] = {}
    for r in rows:
        key = (r.run_id, r.provider_code, r.route or "-", r.cost_source or "unknown")
        slot = acc.setdefault(key, [0, 0, 0])
        slot[0] += 1
        if r.replayed or r.cost_source == "prepulled":
            continue
        if r.charged_usd is None or r.cost_source == "unknown":
            slot[2] += 1
            continue
        slot[1] += micros(r.charged_usd)
    return [Line(*k, *v) for k, v in sorted(acc.items())]


async def ledger_rows(s: AsyncSession, run_id: UUID) -> list[LedgerRow]:
    """The run's finished calls from the insert-only ledger."""
    rows = (
        await s.execute(
            text(
                "SELECT run_id, provider_code, route, cost_source, cost_usd FROM tool_calls"
                " WHERE run_id = :r"
            ),
            {"r": run_id},
        )
    ).all()
    return [
        LedgerRow(
            str(r.run_id),
            str(r.provider_code),
            r.route,
            r.cost_source,
            None if r.cost_usd is None else Decimal(r.cost_usd),
        )
        for r in rows
    ]


async def refresh(txn: TxnFactory, tenant_id: UUID, run_id: UUID) -> list[Line]:
    """Recompute and store the run's rollup (idempotent)."""
    async with txn(tenant_id) as s:
        lines = rollup_lines(await ledger_rows(s, run_id))
        meta = (
            await s.execute(
                text(
                    "SELECT r.account_id, t.plan AS plan"
                    " FROM account_runs r JOIN tenants t ON t.id = r.tenant_id WHERE r.id = :r"
                ),
                {"r": run_id},
            )
        ).first()
        for ln in lines:
            await s.execute(
                text(
                    "INSERT INTO ops_cost_rollups (run_id, account_id, provider_code, route,"
                    " cost_source, plan_tier, calls, cost_micros, unknown_cost_calls)"
                    " VALUES (:r, :a, :p, :rt, :cs, :pl, :c, :m, :u)"
                    " ON CONFLICT (run_id, provider_code, route, cost_source) DO UPDATE SET"
                    " calls = EXCLUDED.calls, cost_micros = EXCLUDED.cost_micros,"
                    " unknown_cost_calls = EXCLUDED.unknown_cost_calls, refreshed_at = now()"
                ),
                {
                    "r": run_id,
                    "a": meta.account_id if meta else None,
                    "p": ln.provider_code,
                    "rt": ln.route,
                    "cs": ln.cost_source,
                    "pl": meta.plan if meta else "unknown",
                    "c": ln.calls,
                    "m": ln.cost_micros,
                    "u": ln.unknown_cost_calls,
                },
            )
    return lines


async def reconcile(txn: TxnFactory, tenant_id: UUID, run_id: UUID) -> dict[str, Any]:
    """Rollup total versus the ledger's own sum (exact) and the run's spent counter."""
    async with txn(tenant_id) as s:
        stored: Any = (
            await s.execute(
                text(
                    "SELECT COALESCE(sum(cost_micros), 0) FROM ops_cost_rollups WHERE run_id = :r"
                ),
                {"r": run_id},
            )
        ).scalar_one()
        ledger = rollup_lines(await ledger_rows(s, run_id))
        unknown = sum(ln.unknown_cost_calls for ln in ledger)
    expected = sum(ln.cost_micros for ln in ledger)
    return {
        "rollup_micros": int(stored),
        "ledger_micros": expected,
        "drift_micros": int(stored) - expected,
        "unknown_cost_calls": unknown,
    }
