"""Nightly checks the worker image runs as one-shot commands (Cloud Run jobs started by Cloud
Scheduler, or the local scheduler service).

``spend_invariants`` checks every ledger rule across every tenant:

* the paid-call ledger of each run created in the window (``spend.jobs.spend_invariants``: spent
  equals the ledger, the parts sum to it, the enforcement columns match, stale reservations);
* the credit pool ledger of each tenant with credit activity in the window
  (``replica.credits.ledger_problems``: open jobs hold their estimate, ended jobs released what
  they held and settled what they used, nothing negative), read inside the tenant's own
  transaction. The tenants come from the identifier-only lookup
  ``tenants_with_credit_activity``.

The result carries identifiers and rule names only, never amounts or names.

GUARANTEE(test_the_nightly_check_flags_a_broken_credit_ledger)
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[Any]]
SessionFactory = Callable[[], AbstractAsyncContextManager[Any]]


@dataclass
class InvariantReport:
    """What one nightly pass found."""

    runs_checked: int = 0
    runs_failed: list[UUID] = field(default_factory=list)
    tenants_checked: int = 0
    credit_problems: list[str] = field(default_factory=list)
    exit_reason: str = "converged"

    @property
    def ok(self) -> bool:
        """True when no ledger rule is broken."""
        return not (self.runs_failed or self.credit_problems)

    def line(self) -> str:
        """One summary line (counts only)."""
        return (
            f"runs_checked={self.runs_checked} runs_failed={len(self.runs_failed)}"
            f" tenants_checked={self.tenants_checked}"
            f" credit_problems={len(self.credit_problems)} exit={self.exit_reason}"
        )


async def spend_invariants(
    sessions: SessionFactory,
    txn: TxnFactory,
    now: datetime,
    *,
    window_days: int = 2,
    credit_window_days: int = 40,
) -> InvariantReport:
    """One pass over every tenant's ledgers."""
    from account_intelligence.replica.credits import ledger_problems
    from spend.jobs import spend_invariants as run_pass

    report = InvariantReport()
    runs = await run_pass(sessions, txn, now, window_days=window_days)
    report.runs_checked = runs.checked
    report.runs_failed = list(runs.failed)
    report.exit_reason = runs.exit_reason
    async with sessions() as s:
        tenants = list(
            (
                await s.execute(
                    text("SELECT tenant_id FROM tenants_with_credit_activity(:since)"),
                    {"since": now - timedelta(days=credit_window_days)},
                )
            )
            .scalars()
            .all()
        )
    # LOOP(credit ledger pass): cap=the tenants with credit activity in the window; exit=end
    for tenant in tenants:
        async with txn(tenant) as s:
            found = await ledger_problems(s)
        report.tenants_checked += 1
        report.credit_problems.extend(f"{tenant}:{p}" for p in found)
    return report
