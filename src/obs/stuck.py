"""The stuck-run detector (O12): read-only classification; the reconciler stays the only repairer.

``classify`` (pure) flags QUEUED runs older than the reconciler's 60 s, RUNNING runs with no slice
or unit event for ``running_quiet_s`` (a configured multiple of the 240 s slice), AWAITING_APPROVAL
past its deadline, and expired leases. A run paused within its deadline is never stuck. ``scan``
reads runs in bounded keyset pages and records each observation as an insert-only row.

GUARANTEE(test_stuck_classification_never_flags_a_paused_or_busy_run): O12.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

QUEUED_GRACE_S = 60  # ARCH: the reconciler re-sends runs QUEUED for more than 60 s
TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True)
class RunView:
    """What the detector reads about one run."""

    run_id: str
    status: str
    created_at: datetime
    last_activity_at: datetime | None
    approval_deadline: datetime | None


def age_bucket(seconds: float) -> str:
    """The metric's age bucket."""
    if seconds < 300:
        return "lt_5m"
    if seconds < 3600:
        return "5m_1h"
    if seconds < 86400:
        return "1h_24h"
    return "gt_24h"


def classify(run: RunView, now: datetime, running_quiet_s: float) -> str | None:
    """The stuck reason, or None."""
    if run.status == "QUEUED" and (now - run.created_at).total_seconds() > QUEUED_GRACE_S:
        return "queued_too_long"
    if run.status in ("RUNNING", "DISTILLING", "VERIFYING"):
        last = run.last_activity_at or run.created_at
        if (now - last).total_seconds() > running_quiet_s:
            return "no_activity"
    if (
        run.status == "AWAITING_APPROVAL"
        and run.approval_deadline is not None
        and now > run.approval_deadline
    ):
        return "approval_past_deadline"
    return None


async def scan(
    txn: TxnFactory, tenant_id: UUID, now: datetime, running_quiet_s: float, page: int = 500
) -> list[tuple[str, str, str]]:
    """One bounded pass over the tenant's live runs; returns (run, reason, age bucket) and
    records each observation."""
    found: list[tuple[str, str, str]] = []
    after = "00000000-0000-0000-0000-000000000000"
    # LOOP(stuck scan): cap=keyset pages of `page` runs until none remain; exit=all scanned
    while True:
        async with txn(tenant_id) as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT r.id, r.status, r.created_at, r.approval_deadline,"
                        " (SELECT max(e.at) FROM work_unit_events e"
                        "  WHERE e.run_id = r.id) AS last_unit"
                        " FROM account_runs r WHERE r.status NOT IN ('COMPLETED', 'PARTIAL',"
                        " 'BUDGET_EXCEEDED', 'FAILED', 'CANCELED') AND r.id > CAST(:after AS uuid)"
                        " ORDER BY r.id LIMIT :n"
                    ),
                    {"after": after, "n": page},
                )
            ).all()
            for r in rows:
                view = RunView(str(r.id), r.status, r.created_at, r.last_unit, r.approval_deadline)
                reason = classify(view, now, running_quiet_s)
                if reason is None:
                    continue
                bucket = age_bucket((now - r.created_at).total_seconds())
                found.append((str(r.id), reason, bucket))
                await s.execute(
                    text(
                        "INSERT INTO stuck_run_observations (run_id, state, reason, age_bucket, at)"
                        " VALUES (:r, :s, :why, :b, :at)"
                    ),
                    {"r": r.id, "s": r.status, "why": reason, "b": bucket, "at": now},
                )
        if len(rows) < page:
            return found
        after = str(rows[-1].id)


def quiet_seconds(slice_s: float, multiple: float) -> float:
    """``running_quiet_s`` from the slice length and its configured multiple."""
    return float(timedelta(seconds=slice_s * multiple).total_seconds())
