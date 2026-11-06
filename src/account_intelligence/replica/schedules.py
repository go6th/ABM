"""Scheduled refreshes: each account's cadence, when it next runs, what the schedule costs a
month, and the scheduler tick that starts due refreshes from the client's credit pool.

* A cadence is one of ``3d``, ``weekly``, ``2w``, ``monthly`` or ``off``. Anything faster than the
  client's fastest allowed schedule is refused. Saving a cadence sets the next refresh to the last
  research plus the cadence (or now, when that is already past).
* Estimated use a month: for every scheduled account, the refreshes a month at its cadence times
  its own estimate for its refresh scope (``credits.estimate``), rounded to the nearest ten.
* The tick (``POST /tasks/schedules-tick``, Cloud Scheduler in production) finds due accounts
  through the identifier-only lookup ``due_scheduled_refreshes``, then in each client's own
  transaction counts one of the account owner's researches and records the refresh's credits on
  the ledger (owner decision 2026-10-14: researches, not the credit pool, limit a user). With no
  research left the refresh is skipped this cycle and the owner is told
  (``research_limit_reached``). Either way the account's next refresh moves on by its cadence,
  so a used-up limit never piles up jobs.
* One tick is bounded (fix prompt A 2026-11-03, ADR-0108): at most
  ``platform_defaults.schedule_tick_max_starts`` starts (default 10, scheduled refreshes and
  promoted waiting jobs together) and at most ``schedule_tick_max_per_tenant`` (default 3) for one
  client. Due accounts are taken round robin across clients, each client's oldest first
  (``fair_order``). An account over the ceiling is not touched: it keeps its next refresh and is
  due again on the next tick; a waiting job over the ceiling stays waiting.
* The tick starts nothing unless ``RESEARCH_AUTOMATIC_STARTS=on`` (``runs.start_guard``, owner's
  rule 2026-11-05: nothing paid starts without a person saying yes twice). Only the scheduler
  makes waiting jobs, so the promotion below is off with it.

GUARANTEE(test_a_due_refresh_with_no_research_left_is_skipped_and_the_owner_told)
GUARANTEE(test_25_due_accounts_start_at_most_10_per_tick_and_3_per_tenant)
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from account_intelligence.db.system_scope import untenanted_session
from account_intelligence.replica import credits as CR
from account_intelligence.replica import limits as LIM
from account_intelligence.replica.notifications import notify
from account_intelligence.replica.settings import CADENCE_DAYS, CADENCE_ORDER, client_settings

CADENCES = (*CADENCE_ORDER, "off")
SEG_LABEL = {
    "3d": "3 days",
    "weekly": "Weekly",
    "2w": "2 weeks",
    "monthly": "Monthly",
    "off": "Off",
}
FASTEST_LABEL = {
    "3d": "every 3 days",
    "weekly": "weekly",
    "2w": "every 2 weeks",
    "monthly": "monthly",
}


class CadenceNotAllowed(ValueError):
    """Faster than the client's fastest allowed schedule."""


def next_label(next_at: datetime | None, cadence: str, now: datetime) -> str:
    """'Next refresh in 5 days', 'Next refresh tomorrow', 'Due now', 'No scheduled refresh'."""
    if cadence == "off" or next_at is None:
        return "No scheduled refresh"
    days = math.ceil((next_at - now).total_seconds() / 86400)
    if days <= 0:
        return "Due now"
    if days == 1:
        return "Next refresh tomorrow"
    return f"Next refresh in {days} days"


async def _last_research(session: Any, account_id: UUID) -> datetime | None:
    value = await session.scalar(
        text(
            "SELECT max(f) FROM (SELECT finished_at AS f FROM account_runs WHERE account_id = :a"
            " AND status IN ('COMPLETED', 'PARTIAL') UNION ALL SELECT finished_at FROM"
            " research_jobs"
            " WHERE account_id = :a AND status = 'done') x"
        ),
        {"a": account_id},
    )
    return value if isinstance(value, datetime) else None


async def view(session: Any, now: datetime) -> dict[str, Any]:
    """Every account's schedule, the monthly estimate and the pool."""
    settings = await client_settings(session)
    rows = (
        await session.execute(
            text(
                "SELECT id, domain, coalesce(name, domain) AS name, coalesce(refresh_cadence,"
                " 'off') AS cadence,"
                " refresh_scope, next_refresh_at FROM accounts WHERE deleted_at IS NULL"
                " ORDER BY coalesce(name, domain)"
            )
        )
    ).all()
    out, monthly = [], 0.0
    for r in rows:
        cadence = r.cadence if r.cadence in CADENCES else "off"
        if cadence != "off":
            est = await CR.estimate(session, r.id, r.refresh_scope)
            monthly += 30 / CADENCE_DAYS[cadence] * est.credits
        out.append(
            {
                "account_id": str(r.id),
                "domain": r.domain,
                "name": r.name,
                "cadence": cadence,
                "next_label": next_label(r.next_refresh_at, cadence, now),
                "next_refresh_at": None
                if r.next_refresh_at is None or cadence == "off"
                else r.next_refresh_at.isoformat(),
                "options": [
                    {"cadence": c, "label": SEG_LABEL[c], "allowed": settings.allows_cadence(c)}
                    for c in CADENCES
                ],
            }
        )
    pool = await CR.pool(session, now)
    return {
        "rows": out,
        "fastest": settings.fastest_schedule,
        "fastest_label": FASTEST_LABEL[settings.fastest_schedule],
        "estimate_month": int(round(monthly / 10.0) * 10),
        "pool": pool["pool"],
        "resets_on": pool["resets_on"],
    }


async def set_cadence(session: Any, account_id: UUID, cadence: str, now: datetime) -> None:
    """Save one account's cadence and its next refresh."""
    settings = await client_settings(session)
    if not settings.allows_cadence(cadence):
        raise CadenceNotAllowed(cadence)
    next_at = None
    if cadence != "off":
        last = await _last_research(session, account_id) or now
        next_at = max(now, last + timedelta(days=CADENCE_DAYS[cadence]))
    await session.execute(
        text(
            "UPDATE accounts SET refresh_cadence = :c, next_refresh_at = :n, updated_at = :now"
            " WHERE id = :a"
        ),
        {"c": cadence, "n": next_at, "now": now, "a": account_id},
    )


Start = Callable[[UUID, UUID], Awaitable[None]]


@dataclass
class StartBudget:
    """How many runs one tick may still start: overall and per client."""

    max_starts: int
    max_per_tenant: int
    started: int = 0
    per_tenant: Counter[UUID] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        if self.max_starts < 1 or self.max_per_tenant < 1:
            raise ValueError("a tick ceiling must be at least 1")

    @property
    def exhausted(self) -> bool:
        """No start is left for anyone in this tick."""
        return self.started >= self.max_starts

    def room(self, tenant_id: UUID) -> int:
        """Starts this client may still have in this tick."""
        return max(
            0,
            min(self.max_starts - self.started, self.max_per_tenant - self.per_tenant[tenant_id]),
        )

    def take(self, tenant_id: UUID) -> None:
        """Count one start for the client (a start without room is a bug, never silent)."""
        if self.room(tenant_id) < 1:
            raise RuntimeError("schedule tick start over its ceiling")
        self.started += 1
        self.per_tenant[tenant_id] += 1


def fair_order(due: Sequence[tuple[UUID, UUID]]) -> list[tuple[UUID, UUID]]:
    """Round robin across clients: every client's first due account, then every second one, and
    so on; each client's own order (oldest due first, as the lookup returns them) is kept."""
    rank: Counter[UUID] = Counter()
    keyed = []
    for position, (tenant_id, account_id) in enumerate(due):
        keyed.append((rank[tenant_id], position, (tenant_id, account_id)))
        rank[tenant_id] += 1
    return [row for _, _, row in sorted(keyed)]


async def tick_bounds(session: Any) -> StartBudget:
    """The tick's ceilings from ``platform_defaults`` (migration 0098)."""
    row = (
        await session.execute(
            text(
                "SELECT schedule_tick_max_starts, schedule_tick_max_per_tenant"
                " FROM platform_defaults WHERE id = 1"
            )
        )
    ).one()
    return StartBudget(int(row[0]), int(row[1]))


async def tick(now: datetime, start: Start) -> dict[str, int]:
    """Start the due scheduled refreshes the pools can cover, within the tick's ceilings; queue
    the rest as waiting. Accounts over the ceiling are left due for the next tick."""
    from account_intelligence.runs.start_guard import automatic_starts_allowed

    if not automatic_starts_allowed():
        return {"started": 0, "waiting": 0, "promoted": 0, "deferred": 0}
    async with untenanted_session() as plain:
        rows = await plain.execute(text("SELECT * FROM due_scheduled_refreshes(:n)"), {"n": now})
        due = fair_order([(r.tenant_id, r.account_id) for r in rows])
        budget = await tick_bounds(plain)
    started = waiting = deferred = 0
    for index, (tenant_id, account_id) in enumerate(due):
        if budget.exhausted:
            deferred += len(due) - index
            break
        if budget.room(tenant_id) < 1:
            deferred += 1
            continue
        job: tuple[UUID, str] | None = None
        async with tenant_txn(tenant_id) as s:
            acct = (
                await s.execute(
                    text(
                        "SELECT refresh_cadence, refresh_scope, added_by, owner_user_id,"
                        " next_refresh_at FROM accounts"
                        " WHERE id = :a AND deleted_at IS NULL FOR UPDATE"
                    ),
                    {"a": account_id},
                )
            ).first()
            if (
                acct is None
                or acct.refresh_cadence not in CADENCE_DAYS
                or acct.next_refresh_at > now
            ):
                continue
            est = await CR.estimate(s, account_id, acct.refresh_scope)
            # a scheduled refresh is one of the owner's researches (owner decision 2026-10-14):
            # counted with its job in one savepoint (an open job undoes the count), and with none
            # left it is skipped this cycle and the owner is told
            try:
                async with s.begin_nested():
                    await LIM.take(s, str(acct.owner_user_id), "researches", now)
                    job = await CR.reserve(
                        s,
                        account_id=account_id,
                        kind="scheduled_refresh",
                        scope=acct.refresh_scope,
                        credits=est.credits,
                        actor="scheduler",
                        now=now,
                        wait_if_short=True,
                        enforce_pool=False,
                    )
            except CR.JobOpen:
                continue
            except LIM.LimitReached:
                await s.execute(
                    text("UPDATE accounts SET next_refresh_at = :n WHERE id = :a"),
                    {
                        "n": now + timedelta(days=CADENCE_DAYS[acct.refresh_cadence]),
                        "a": account_id,
                    },
                )
                await notify(
                    s,
                    user_id=str(acct.owner_user_id),
                    kind="research_limit_reached",
                    account_id=account_id,
                    job_id=None,
                )
                continue
            await s.execute(
                text("UPDATE accounts SET next_refresh_at = :n WHERE id = :a"),
                {"n": now + timedelta(days=CADENCE_DAYS[acct.refresh_cadence]), "a": account_id},
            )
            if job[1] == "waiting_credit_limit" and acct.added_by:
                await notify(
                    s,
                    user_id=acct.added_by,
                    kind="waiting_credit_limit",
                    account_id=account_id,
                    job_id=job[0],
                )
        if job is None:
            continue
        if job[1] == "queued":
            started += 1
            budget.take(tenant_id)
            await start(tenant_id, job[0])
        else:
            waiting += 1
    async with untenanted_session() as plain:
        tenants: list[Any] = list(
            (await plain.execute(text("SELECT tenant_id FROM tenants_with_waiting_jobs()")))
            .scalars()
            .all()
        )
    promoted = 0
    for tenant_id in tenants:
        room = budget.room(tenant_id)
        if room < 1:
            continue  # its waiting jobs stay waiting until a tick has room
        async with tenant_txn(tenant_id) as s:
            queued = await CR.promote_waiting(s, now, limit=room)
        for job_id in queued:
            promoted += 1
            budget.take(tenant_id)
            await start(tenant_id, job_id)
    return {"started": started, "waiting": waiting, "promoted": promoted, "deferred": deferred}
