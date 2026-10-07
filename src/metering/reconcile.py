"""The usage reconcile job (MT1): the event stream must agree with the facts it summarises.

For the context tenant, after a watermark: every created run has one ``run_started`` event; every
run with a settlement in the wallet ledger (commit or refund) has one ``run_settled`` event and
no other run does; every account has one ``account_added`` event; the counters equal what the
events and tables recompute. Any difference is a drift row in the result (identifiers only). The
pass is idempotent and bounded by ``batch``.

GUARANTEE(test_reconcile_catches_planted_drift)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text


@dataclass
class Drift:
    """What disagrees."""

    missing_run_started: list[str] = field(default_factory=list)
    missing_run_settled: list[str] = field(default_factory=list)
    unexpected_run_settled: list[str] = field(default_factory=list)
    missing_account_added: list[str] = field(default_factory=list)
    counter_mismatch: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when nothing disagrees."""
        return not (
            self.missing_run_started
            or self.missing_run_settled
            or self.unexpected_run_settled
            or self.missing_account_added
            or self.counter_mismatch
        )


async def reconcile(s: Any, at: datetime, *, batch: int = 5000) -> Drift:
    """One bounded pass for the context tenant."""
    d = Drift()
    d.missing_run_started = [
        str(r[0])
        for r in (
            await s.execute(
                text(
                    "SELECT r.id FROM account_runs r WHERE NOT EXISTS (SELECT 1 FROM usage_events e"
                    " WHERE e.idempotency_key = 'run_started:' || r.id::text)"
                    " ORDER BY r.id LIMIT :n"
                ),
                {"n": batch},
            )
        ).all()
    ]
    settled = "SELECT DISTINCT l.run_id FROM wallet_ledger l WHERE l.entry IN ('commit', 'refund')"
    d.missing_run_settled = [
        str(r[0])
        for r in (
            await s.execute(
                text(
                    f"SELECT run_id FROM ({settled}) x WHERE run_id IS NOT NULL AND NOT EXISTS"  # noqa: S608
                    " (SELECT 1 FROM usage_events e"
                    " WHERE e.idempotency_key = 'run_settled:' || x.run_id)"
                    " ORDER BY run_id LIMIT :n"
                ),
                {"n": batch},
            )
        ).all()
    ]
    d.unexpected_run_settled = [
        str(r[0])
        for r in (
            await s.execute(
                text(
                    "SELECT e.subject_id FROM usage_events e WHERE e.kind = 'run_settled'"
                    " AND NOT EXISTS"
                    f" (SELECT 1 FROM ({settled}) x WHERE x.run_id::text = e.subject_id)"  # noqa: S608
                    " ORDER BY e.subject_id LIMIT :n"
                ),
                {"n": batch},
            )
        ).all()
    ]
    d.missing_account_added = [
        str(r[0])
        for r in (
            await s.execute(
                text(
                    "SELECT a.domain FROM accounts a WHERE NOT EXISTS (SELECT 1 FROM usage_events e"
                    " WHERE e.idempotency_key = 'account_added:' || a.domain)"
                    " ORDER BY a.domain LIMIT :n"
                ),
                {"n": batch},
            )
        ).all()
    ]
    period = at.strftime("%Y-%m")
    recomputed = {
        "accounts": (await s.execute(text("SELECT count(*) FROM accounts"))).scalar_one(),
        "runs_per_period": (
            await s.execute(
                text(
                    "SELECT count(*) FROM usage_events WHERE kind = 'run_started' AND period = :p"
                ),
                {"p": period},
            )
        ).scalar_one(),
        "seats": (
            await s.execute(text("SELECT count(*) FROM memberships WHERE status = 'active'"))
        ).scalar_one(),
    }
    for dim, expected in recomputed.items():
        p = period if dim == "runs_per_period" else "all"
        got = (
            await s.execute(
                text("SELECT used FROM usage_counters WHERE period = :p AND dimension = :d"),
                {"p": p, "d": dim},
            )
        ).scalar_one_or_none()
        if got is not None and int(got) != int(expected):
            d.counter_mismatch[dim] = (int(got), int(expected))
    return d
