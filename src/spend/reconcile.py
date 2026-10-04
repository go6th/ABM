"""Reconciliation (K7, K9). ``check`` is pure; ``reconcile_run`` reads one run and records what
it finds.

* the run's ``spent_usd`` must equal the ledger total, and the per-provider parts must sum to it;
  a mismatch is ``ReconciliationFailed`` (the report is stamped, never shipped silently);
* an attempt ``started`` longer ago than the lease and never closed is **stale**: its reservation
  stays counted (never under-counted) and is written to ``unreconciled_usd`` with the amount at
  risk listed;
* an approved run whose enforcement columns differ from its latest approved version breaks K1.

GUARANTEE(test_reconcile_detects_every_mismatch): no mismatch passes silently.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from spend.models import SpendLine
from spend.money import micros_to_usd, total, usd_to_micros
from spend.rollup import EventRow, aggregate, ledger_events, open_attempts


@dataclass(frozen=True)
class Reconciliation:
    """The outcome for one run."""

    problems: tuple[str, ...]
    ledger_micros: int
    stale_attempts: int
    stale_micros: int
    by_provider: tuple[SpendLine, ...] = field(default=())

    @property
    def ok(self) -> bool:
        """No problem found."""
        return not self.problems


def check(
    spent_micros: int,
    ledger_micros: int,
    parts: Sequence[SpendLine],
    *,
    cap_columns: tuple[int | None, int | None] | None = None,
    approved: tuple[int | None, int | None] | None = None,
) -> tuple[str, ...]:
    """Problems found (empty when consistent). ``cap_columns`` and ``approved`` are
    (version, cap micros) on the run and of the latest approved version."""
    problems = []
    if spent_micros != ledger_micros:
        problems.append(f"spent {spent_micros} != ledger {ledger_micros}")
    parts_total = total(p.spent_micros for p in parts)
    if parts_total != ledger_micros:
        problems.append(f"parts {parts_total} != ledger {ledger_micros}")
    if cap_columns is not None and approved is not None and cap_columns != approved:
        problems.append(f"run cap {cap_columns} != latest approved {approved}")
    return tuple(problems)


def stale(
    rows: Sequence[EventRow], started_at: dict[tuple[str, int], datetime], now: datetime, ttl_s: int
) -> list[EventRow]:
    """Open attempts older than the lease time."""
    limit = now - timedelta(seconds=ttl_s)
    return [r for r in open_attempts(rows) if started_at.get((r.args_hash, r.attempt), now) < limit]


async def reconcile_run(
    s: AsyncSession, run_id: UUID, now: datetime, lease_ttl_s: int
) -> Reconciliation:
    """Read the run, check it, and record the stale reservations (one conditional UPDATE)."""
    rows = await ledger_events(s, run_id)
    times = {
        (r.args_hash, r.attempt): r.created_at
        for r in (
            await s.execute(
                text(
                    "SELECT args_hash, attempt, created_at FROM tool_call_events WHERE run_id = :r"
                    " AND event = 'started'"
                ),
                {"r": run_id},
            )
        ).all()
    }
    parts = aggregate(rows, lambda r: r.provider_code)
    ledger = total(p.spent_micros for p in parts)
    run: Any = (
        await s.execute(
            text("SELECT spent_usd, cap_version, budget_usd FROM account_runs WHERE id = :r"),
            {"r": run_id},
        )
    ).first()
    approved: Any = (
        await s.execute(
            text(
                "SELECT v.version, v.cap_usd FROM run_cap_versions v JOIN run_cap_events e"
                " ON e.run_id = v.run_id AND e.version = v.version AND e.event = 'approved'"
                " WHERE v.run_id = :r ORDER BY v.version DESC LIMIT 1"
            ),
            {"r": run_id},
        )
    ).first()
    connector_sum: Any = (
        await s.execute(
            text(
                "SELECT COALESCE(SUM(e.charged_usd) FILTER (WHERE e.event IN ('done', 'failed',"
                " 'abandoned')), 0) + COALESCE(SUM(e.reserved_usd) FILTER (WHERE e.event ="
                " 'started' AND NOT EXISTS (SELECT 1 FROM tool_call_events t WHERE t.run_id ="
                " e.run_id AND t.args_hash = e.args_hash AND t.attempt = e.attempt AND t.event IN"
                " ('done', 'failed', 'abandoned'))), 0) FROM tool_call_events e WHERE e.run_id = :r"
            ),
            {"r": run_id},
        )
    ).scalar_one()
    problems = list(
        check(
            usd_to_micros(run.spent_usd) if run is not None else 0,
            ledger,
            parts,
            cap_columns=(
                (
                    run.cap_version,
                    usd_to_micros(run.budget_usd) if run.budget_usd is not None else None,
                )
                if run is not None and run.cap_version is not None
                else None
            ),
            approved=(
                (approved.version, usd_to_micros(approved.cap_usd))
                if approved is not None
                else None
            ),
        )
    )
    if usd_to_micros(connector_sum) != ledger:
        problems.append("rollup differs from the connector ledger sum")
    old = stale(rows, times, now, lease_ttl_s)
    stale_micros = total(r.reserved_micros for r in old)
    await s.execute(
        text(
            "UPDATE account_runs SET unreconciled_usd = :u WHERE id = :r AND unreconciled_usd <> :u"
        ),
        {"u": micros_to_usd(stale_micros), "r": run_id},
    )
    return Reconciliation(tuple(problems), ledger, len(old), stale_micros, parts)
