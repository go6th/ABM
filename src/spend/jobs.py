"""``spend_invariants``: the nightly invariant pass (a Cloud Run job started by Cloud Scheduler).

For each run created in the window (identifiers from ``spend_runs_to_check``, a narrow definer
function), under that run's own tenant context: reconcile the run (spent equals the ledger, the
parts sum to it, the enforcement columns equal the latest approved version, stale reservations
recorded). Bounded by ``batch_size`` runs per pass; the exit reason is recorded. Metrics carry
the outcome only (no tenant label, no provider name, no amount).

GUARANTEE(test_nightly_pass_flags_a_mismatched_run): a broken run is reported, never skipped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from spend.reconcile import reconcile_run

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class PassResult:
    """What one pass found (identifiers and counts only)."""

    checked: int = 0
    failed: list[UUID] = field(default_factory=list)
    stale_runs: list[UUID] = field(default_factory=list)
    exit_reason: str = "converged"


async def spend_invariants(
    sessions: SessionFactory,
    txn: TxnFactory,
    now: datetime,
    *,
    window_days: int = 2,
    batch_size: int = 500,
    lease_ttl_s: int = 60,
) -> PassResult:
    """One bounded pass over the runs created in the window."""
    out = PassResult()
    async with sessions() as s:
        rows = (
            await s.execute(
                text("SELECT tenant_id, run_id FROM spend_runs_to_check(:since) LIMIT :n"),
                {"since": now - timedelta(days=window_days), "n": batch_size + 1},
            )
        ).all()
    if len(rows) > batch_size:
        out.exit_reason = "cap_reached"
        rows = rows[:batch_size]
    # LOOP(spend invariant pass): cap=batch_size runs; exit=converged, cap_reached
    for row in rows:
        async with txn(row.tenant_id) as s:
            result = await reconcile_run(s, row.run_id, now, lease_ttl_s)
        out.checked += 1
        if not result.ok:
            out.failed.append(row.run_id)
        if result.stale_attempts:
            out.stale_runs.append(row.run_id)
    return out


def main() -> int:  # pragma: no cover - the job entry point (exercised by the job, not tests)
    """Entry point: one pass with the application's engine; exit 1 when a run failed."""
    from datetime import UTC

    from account_intelligence.db.session import SessionLocal, tenant_txn

    result = asyncio.run(spend_invariants(SessionLocal, tenant_txn, datetime.now(UTC)))
    print(
        f"checked={result.checked} failed={len(result.failed)} stale={len(result.stale_runs)}"
        f" exit={result.exit_reason}"
    )
    return 1 if result.failed else 0
