"""Research credits: the client's monthly pool, estimates, and the reserve, settle and release
ledger (``credit_entries``, append-only).

* Pool: ``client_settings.monthly_credit_pool`` per calendar month (UTC); it resets on the 1st.
  For a period, held = reserved - released, used = settled, left = pool - used - held.
* Estimate: the average credits used by the account's last finished jobs of the same scope, else
  the rate table (``config/credits.yaml``), never above the platform's credit cap per pull.
* Reserve (on Confirm): one tenant transaction locks the client's settings row (so two confirms
  can never both spend the last credits), checks what is left, writes the job and its reserve.
* Settle (when the job ends): credits used = ceil(real spend / credit value), never more than
  reserved; the reserve is released in the same transaction. A failed job settles what it used.

GUARANTEE(test_two_confirms_cannot_spend_the_same_credits)
GUARANTEE(test_settle_never_charges_more_than_was_reserved)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import text

from account_intelligence.replica.settings import client_settings

CONFIG = Path(__file__).resolve().parents[3] / "config" / "credits.yaml"
SCOPES = ("jobs", "people", "full")
PURPOSE = {
    "web_refresh": "web_refresh",
    "scheduled_refresh": "scheduled_refresh",
    "new_research": "new_research",
    "file_analysis": "file_analysis",
}


class CreditsShort(Exception):
    """The pool cannot cover the reservation."""

    def __init__(self, needed: int, left: int) -> None:
        super().__init__("credits short")
        self.needed = needed
        self.left = left


class JobOpen(Exception):
    """The account already has a research job waiting or running."""


@dataclass(frozen=True)
class Rate:
    """Credits and minutes for one kind of job."""

    credits: int
    minutes: int


@dataclass(frozen=True)
class Rates:
    """The rate table and the one credit value."""

    credit_value_usd: Decimal
    estimates: dict[str, Rate]
    history_jobs: int


@lru_cache(maxsize=1)
def rates() -> Rates:
    """``config/credits.yaml``, validated once (an invalid file fails at startup)."""
    raw = yaml.safe_load(CONFIG.read_text())
    value = Decimal(str(raw["credit_value_usd"]))
    if value <= 0:
        raise ValueError("credit_value_usd must be positive")
    table = {k: Rate(int(v["credits"]), int(v["minutes"])) for k, v in raw["estimates"].items()}
    for key in (*SCOPES, "new_research", "file_public"):
        if key not in table or table[key].credits < 1 or table[key].minutes < 1:
            raise ValueError(f"credits.yaml needs a positive estimate for {key}")
    return Rates(value, table, max(1, int(raw.get("history_jobs", 3))))


def usd_to_credits(usd: Decimal | float) -> int:
    """Real spend in credits (rounded up: a partial credit is a credit)."""
    spend = Decimal(str(usd))
    return 0 if spend <= 0 else math.ceil(spend / rates().credit_value_usd)


def period_of(at: datetime) -> str:
    """The pool period an instant belongs to: 'YYYY-MM' in UTC."""
    return at.astimezone(UTC).strftime("%Y-%m")


def resets_on(at: datetime) -> date:
    """The 1st of the next month."""
    d = at.astimezone(UTC).date()
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


async def credit_cap_per_pull(session: Any) -> int:
    """The platform's cap on one job's credits."""
    value = await session.scalar(
        text("SELECT credit_cap_per_pull FROM platform_defaults WHERE id = 1")
    )
    return int(value or 400)


async def pool(session: Any, now: datetime) -> dict[str, Any]:
    """The caller tenant's pool for the current period."""
    settings = await client_settings(session)
    period = period_of(now)
    row = (
        await session.execute(
            text(
                "SELECT coalesce(sum(credits) FILTER (WHERE kind = 'reserve'), 0) AS reserved,"
                " coalesce(sum(credits) FILTER (WHERE kind = 'release'), 0) AS released,"
                " coalesce(sum(credits) FILTER (WHERE kind = 'settle'), 0) AS used"
                " FROM credit_entries WHERE period = :p"
            ),
            {"p": period},
        )
    ).one()
    held = int(row.reserved) - int(row.released)
    used = int(row.used)
    total = settings.monthly_credit_pool
    return {
        "period": period,
        "pool": total,
        "used": used,
        "held": held,
        "left": max(0, total - used - held),
        "resets_on": resets_on(now).isoformat(),
    }


async def estimate(session: Any, account_id: UUID | None, kind: str) -> Rate:
    """Credits and minutes for one job: the account's history of this kind, else the rate."""
    table = rates()
    base = table.estimates[kind]
    credits = base.credits
    if account_id is not None and kind in SCOPES:
        past = (
            (
                await session.execute(
                    text(
                        "SELECT credits_used FROM research_jobs WHERE account_id = :a AND scope ="
                        " :s"
                        " AND status = 'done' AND credits_used > 0 ORDER BY finished_at DESC LIMIT"
                        " :n"
                    ),
                    {"a": account_id, "s": kind, "n": table.history_jobs},
                )
            )
            .scalars()
            .all()
        )
        if past:
            credits = math.ceil(sum(past) / len(past))
    cap = await credit_cap_per_pull(session)
    return Rate(max(1, min(credits, cap)), base.minutes)


async def open_job(session: Any, account_id: UUID) -> Any:
    """The account's job waiting for credits, queued or running, if any."""
    return (
        await session.execute(
            text(
                "SELECT id, kind, scope, status, credits_estimated, created_at FROM research_jobs"
                " WHERE account_id = :a AND status IN ('waiting_credit_limit', 'queued', 'running')"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"a": account_id},
        )
    ).first()


async def reserve(
    session: Any,
    *,
    account_id: UUID,
    kind: str,
    scope: str,
    credits: int,
    actor: str,
    now: datetime,
    request_id: UUID | None = None,
    from_turn_id: UUID | None = None,
    wait_if_short: bool = False,
) -> tuple[UUID, str]:
    """Create the job and reserve its credits in the caller's transaction.

    Returns (job id, status): ``queued`` when reserved, ``waiting_credit_limit`` when the pool is
    short and ``wait_if_short`` (the scheduler) asked to wait; otherwise ``CreditsShort``. A
    repeated ``request_id`` returns the first job. ``JobOpen`` when the account already has one
    open."""
    if request_id is not None:
        prior = (
            await session.execute(
                text("SELECT id, status FROM research_jobs WHERE request_id = :r"),
                {"r": request_id},
            )
        ).first()
        if prior is not None:
            return UUID(str(prior.id)), str(prior.status)
    await client_settings(session)
    await session.execute(text("SELECT 1 FROM client_settings FOR UPDATE"))
    if await open_job(session, account_id) is not None:
        raise JobOpen
    left = (await pool(session, now))["left"]
    status = "queued" if credits <= left else "waiting_credit_limit"
    if status == "waiting_credit_limit" and not wait_if_short:
        raise CreditsShort(credits, left)
    period = period_of(now)
    job_id = (
        await session.execute(
            text(
                "INSERT INTO research_jobs (account_id, kind, scope, status, period,"
                " credits_estimated,"
                " requested_by, request_id, from_turn_id, created_at) VALUES (:a, :k, :s, :st, :p,"
                " :c, :by,"
                " :r, :t, :now) RETURNING id"
            ),
            {
                "a": account_id,
                "k": kind,
                "s": scope,
                "st": status,
                "p": period,
                "c": credits,
                "by": actor,
                "r": request_id,
                "t": from_turn_id,
                "now": now,
            },
        )
    ).scalar_one()
    if status == "queued":
        await _entry(session, period, "reserve", credits, job_id, kind, actor, now)
    return UUID(str(job_id)), status


async def _entry(
    session: Any,
    period: str,
    kind: str,
    credits: int,
    job_id: Any,
    purpose: str,
    actor: str,
    now: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO credit_entries (period, kind, credits, job_id, purpose, actor, at)"
            " VALUES (:p, :k, :c, :j, :pu, :a, :now) ON CONFLICT (tenant_id, job_id, kind) DO"
            " NOTHING"
        ),
        {
            "p": period,
            "k": kind,
            "c": credits,
            "j": job_id,
            "pu": PURPOSE[purpose],
            "a": actor,
            "now": now,
        },
    )


async def settle(
    session: Any,
    job_id: UUID,
    *,
    spent_usd: Decimal,
    now: datetime,
    status: str = "done",
    reason: str | None = None,
) -> int:
    """End a job: charge what it really used (never more than reserved), release the reserve.
    Returns the credits charged. Idempotent: a job already ended is left as it is."""
    job = (
        await session.execute(
            text(
                "SELECT id, kind, period, credits_estimated, status, requested_by FROM"
                " research_jobs"
                " WHERE id = :j FOR UPDATE"
            ),
            {"j": job_id},
        )
    ).first()
    if job is None or job.status in ("done", "failed", "canceled"):
        return 0
    reserved = int(job.credits_estimated) if job.status in ("queued", "running") else 0
    used = min(usd_to_credits(spent_usd), reserved)
    if reserved:
        if used:
            await _entry(session, job.period, "settle", used, job.id, job.kind, "system", now)
        await _entry(session, job.period, "release", reserved, job.id, job.kind, "system", now)
    await session.execute(
        text(
            "UPDATE research_jobs SET status = :st, credits_used = :u, finished_at = :now,"
            " failure_reason = :r WHERE id = :j"
        ),
        {"st": status, "u": used, "now": now, "r": reason, "j": job.id},
    )
    return used


async def promote_waiting(session: Any, now: datetime) -> list[UUID]:
    """Reserve credits for jobs that were waiting, oldest first, while the pool covers them
    (called when credits free up and when a new period starts). Returns the jobs now queued."""
    await client_settings(session)
    await session.execute(text("SELECT 1 FROM client_settings FOR UPDATE"))
    queued: list[UUID] = []
    waiting = (
        await session.execute(
            text(
                "SELECT id, kind, credits_estimated, requested_by FROM research_jobs"
                " WHERE status = 'waiting_credit_limit' ORDER BY created_at"
            )
        )
    ).all()
    for job in waiting:
        left = (await pool(session, now))["left"]
        if job.credits_estimated > left:
            break
        period = period_of(now)
        await session.execute(
            text("UPDATE research_jobs SET status = 'queued', period = :p WHERE id = :j"),
            {"p": period, "j": job.id},
        )
        await _entry(
            session,
            period,
            "reserve",
            int(job.credits_estimated),
            job.id,
            job.kind,
            str(job.requested_by),
            now,
        )
        queued.append(UUID(str(job.id)))
    return queued
