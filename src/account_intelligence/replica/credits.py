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
* One credit truth: this pool and ``credit_entries`` carry every credit movement. Every research
  run belongs to one job (``research_jobs.run_id``); the run lifecycle reserves through
  ``reserve`` when a run is created without a confirmed job and settles through ``settle_run``
  at the run's one settlement. A library add is a job that used nothing. The old wallet tables
  are read-only history.
* ``ledger_problems`` lists every broken ledger rule of the caller tenant (the nightly check).

GUARANTEE(test_two_confirms_cannot_spend_the_same_credits)
GUARANTEE(test_settle_never_charges_more_than_was_reserved)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import text

from account_intelligence.config import CONFIG_DIR
from account_intelligence.replica.settings import client_settings

CONFIG = CONFIG_DIR / "credits.yaml"
SCOPES = ("jobs", "people", "full", "topic")
PURPOSE = {
    "web_refresh": "web_refresh",
    "scheduled_refresh": "scheduled_refresh",
    "new_research": "new_research",
    "file_analysis": "file_analysis",
    "library_add": "library_add",
}
# A run's end status as its job's end status.
JOB_END = {
    "COMPLETED": "done",
    "PARTIAL": "done",
    "BUDGET_EXCEEDED": "done",
    "FAILED": "failed",
    "CANCELED": "canceled",
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
                "SELECT id, kind, scope, status, credits_estimated, created_at, topic FROM"
                " research_jobs"
                " WHERE account_id = :a AND status IN ('waiting_credit_limit', 'queued', 'running')"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"a": account_id},
        )
    ).first()


async def has_request(session: Any, request_id: UUID) -> bool:
    """Whether a job was already made for this request (a retried confirm counts nothing)."""
    return bool(
        await session.scalar(
            text("SELECT 1 FROM research_jobs WHERE request_id = :r"), {"r": request_id}
        )
    )


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
    topic: str | None = None,
    one_open_per_account: bool = True,
    models: dict[str, Any] | None = None,
    enforce_pool: bool = True,
    resume_from: UUID | None = None,
) -> tuple[UUID, str]:
    """Create the job and reserve its credits in the caller's transaction.

    Returns (job id, status): ``queued`` when reserved, ``waiting_credit_limit`` when the pool is
    short and ``wait_if_short`` (the scheduler) asked to wait; otherwise ``CreditsShort``. A
    repeated ``request_id`` returns the first job. ``JobOpen`` when the account already has one
    open."""
    await client_settings(session)
    await session.execute(text("SELECT 1 FROM client_settings FOR UPDATE"))
    # the repeated-request check comes AFTER the client lock: two identical requests at the same
    # moment used to both pass it and the second failed on the unique key with a 500 (audit
    # 2026-10-15); now the second waits for the first and returns its job
    if request_id is not None:
        prior = (
            await session.execute(
                text("SELECT id, status FROM research_jobs WHERE request_id = :r"),
                {"r": request_id},
            )
        ).first()
        if prior is not None:
            return UUID(str(prior.id)), str(prior.status)
    if one_open_per_account and await open_job(session, account_id) is not None:
        raise JobOpen
    left = (await pool(session, now))["left"]
    # A user's research is limited by their research limit, not by credits (owner decision
    # 2026-10-14): ``enforce_pool=False`` records the credits on the ledger without gating on them.
    # The research caps off (dev_limits) gate on nothing either.
    from account_intelligence.dev_limits import research_uncapped

    gated = enforce_pool and not research_uncapped()
    status = "queued" if credits <= left or not gated else "waiting_credit_limit"
    if status == "waiting_credit_limit" and not wait_if_short:
        raise CreditsShort(credits, left)
    period = period_of(now)
    job_id = (
        await session.execute(
            text(
                "INSERT INTO research_jobs (account_id, kind, scope, status, period,"
                " credits_estimated,"
                " requested_by, request_id, from_turn_id, created_at, topic, models, resume_from)"
                " VALUES (:a, :k, :s, :st, :p, :c, :by, :r, :t, :now, :topic,"
                " CAST(:models AS jsonb), :resume) RETURNING id"
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
                "topic": topic,
                "now": now,
                "models": json.dumps(models) if models else None,
                "resume": resume_from,
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
    stop_reason: str | None = None,
) -> int:
    """End a job: charge what it really used (never more than reserved), release the reserve.
    Returns the credits charged. Idempotent: a job already ended is left as it is.
    ``stop_reason`` records why it stopped early (``credit_cap_reached``)."""
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
            " failure_reason = :r, stop_reason = :sr WHERE id = :j"
        ),
        {"st": status, "u": used, "now": now, "r": reason, "sr": stop_reason, "j": job.id},
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


async def claim(session: Any, job_id: UUID, now: datetime) -> Any:
    """Move a queued job to running; None when it is not queued (already taken or ended)."""
    return (
        await session.execute(
            text(
                "UPDATE research_jobs SET status = 'running', started_at = :now"
                " WHERE id = :j AND status = 'queued'"
                " RETURNING id, account_id, kind, scope, credits_estimated, requested_by, topic,"
                " models, resume_from"
            ),
            {"j": job_id, "now": now},
        )
    ).first()


async def link_run(session: Any, job_id: UUID, run_id: UUID) -> None:
    """Record the research run that carried out the job."""
    await session.execute(
        text("UPDATE research_jobs SET run_id = :r WHERE id = :j"), {"r": run_id, "j": job_id}
    )


async def start_with_run(session: Any, job_id: UUID, run_id: UUID, now: datetime) -> None:
    """The job is carried out by this run: link it and mark the job running."""
    done = await session.scalar(
        text(
            "UPDATE research_jobs SET run_id = :r, status = 'running',"
            " started_at = coalesce(started_at, :now)"
            " WHERE id = :j AND status IN ('queued', 'running') RETURNING id"
        ),
        {"r": run_id, "j": job_id, "now": now},
    )
    if done is None:
        raise JobOpen("the job is not waiting to run")


@dataclass(frozen=True)
class RunSettlement:
    """A run's settlement on its job (credits)."""

    job_id: UUID
    kind: str
    account_id: UUID
    requested_by: str
    status: str
    reserved: int
    used: int
    replayed: bool

    @property
    def released(self) -> int:
        """Reserved credits that go back to the pool."""
        return self.reserved - self.used


async def settle_run(
    session: Any,
    run_id: UUID,
    *,
    run_status: str,
    spent_usd: Decimal,
    platform_fault: bool,
    now: datetime,
) -> RunSettlement | None:
    """Settle the job a run carried out, once: real spend through the credit value (nothing when
    the platform was at fault), never more than reserved, the rest released. A run that stopped
    at its credits is marked ``credit_cap_reached``. None for a run that has no job (history from
    before the single ledger)."""
    job = (
        await session.execute(
            text(
                "SELECT id, kind, account_id, requested_by, status, credits_estimated, credits_used"
                " FROM research_jobs WHERE run_id = :r"
            ),
            {"r": run_id},
        )
    ).first()
    if job is None:
        return None
    if job.status in ("done", "failed", "canceled"):
        return RunSettlement(
            UUID(str(job.id)),
            job.kind,
            UUID(str(job.account_id)),
            str(job.requested_by),
            job.status,
            int(job.credits_estimated),
            int(job.credits_used),
            True,
        )
    status = JOB_END[run_status]
    used = await settle(
        session,
        UUID(str(job.id)),
        spent_usd=Decimal(0) if platform_fault else spent_usd,
        now=now,
        status=status,
        reason=None if status == "done" else "The research did not finish.",
        stop_reason="credit_cap_reached" if run_status == "BUDGET_EXCEEDED" else None,
    )
    return RunSettlement(
        UUID(str(job.id)),
        job.kind,
        UUID(str(job.account_id)),
        str(job.requested_by),
        status,
        int(job.credits_estimated),
        used,
        False,
    )


async def record_library_add(session: Any, account_id: UUID, actor: str, now: datetime) -> UUID:
    """A ready-made workspace copied in from the library: a job that used no credits."""
    job_id = await session.scalar(
        text(
            "INSERT INTO research_jobs (account_id, kind, scope, status, period,"
            " credits_estimated, credits_used, requested_by, created_at, started_at, finished_at)"
            " VALUES (:a, 'library_add', 'library', 'done', :p, 0, 0, :by, :now, :now, :now)"
            " RETURNING id"
        ),
        {"a": account_id, "p": period_of(now), "by": actor, "now": now},
    )
    return UUID(str(job_id))


async def ledger_problems(session: Any) -> list[str]:
    """Every broken ledger rule of the caller tenant, as ``rule:job id`` (identifiers only).

    * an open job (queued or running) holds exactly its estimate and has not been settled;
    * a job waiting for credits holds nothing;
    * an ended job that held credits released exactly what it held, settled at most that, and
      its ``credits_used`` equals what it settled (so the pool's "used" is the settled sum);
    * a library add used nothing and moved nothing;
    * no period holds a negative amount."""
    rows = (
        await session.execute(
            text(
                "SELECT j.id, j.kind, j.status, j.credits_estimated, j.credits_used,"
                " coalesce(sum(e.credits) FILTER (WHERE e.kind = 'reserve'), -1) AS reserved,"
                " coalesce(sum(e.credits) FILTER (WHERE e.kind = 'release'), -1) AS released,"
                " coalesce(sum(e.credits) FILTER (WHERE e.kind = 'settle'), 0) AS settled"
                " FROM research_jobs j LEFT JOIN credit_entries e ON e.job_id = j.id"
                " GROUP BY j.id ORDER BY j.id"
            )
        )
    ).all()
    out: list[str] = []
    for r in rows:
        jid = str(r.id)
        held = r.reserved >= 0
        if r.kind == "library_add":
            if held or r.released >= 0 or r.settled or r.credits_used:
                out.append(f"library_add_moved_credits:{jid}")
            continue
        if r.status == "waiting_credit_limit":
            if held or r.released >= 0 or r.settled:
                out.append(f"waiting_job_holds_credits:{jid}")
        elif r.status in ("queued", "running"):
            if r.reserved != r.credits_estimated:
                out.append(f"open_job_reserve_wrong:{jid}")
            if r.released >= 0 or r.settled:
                out.append(f"open_job_settled:{jid}")
        else:
            if held and r.released != r.reserved:
                out.append(f"release_differs_from_reserve:{jid}")
            if not held and (r.released >= 0 or r.settled):
                out.append(f"settled_without_reserve:{jid}")
            if r.settled > max(r.reserved, 0):
                out.append(f"settled_more_than_reserved:{jid}")
            if r.settled != r.credits_used:
                out.append(f"used_differs_from_settled:{jid}")
    periods = (
        await session.execute(
            text(
                "SELECT period,"
                " coalesce(sum(credits) FILTER (WHERE kind = 'reserve'), 0)"
                " - coalesce(sum(credits) FILTER (WHERE kind = 'release'), 0) AS held"
                " FROM credit_entries GROUP BY period"
            )
        )
    ).all()
    out.extend(f"negative_hold:{p.period}" for p in periods if p.held < 0)
    return out


async def account_label(s: Any, account_id: UUID) -> Any:
    """The account's domain and display name (the domain when it has no name)."""
    return (
        await s.execute(
            text("SELECT domain, coalesce(name, domain) AS name FROM accounts WHERE id = :a"),
            {"a": account_id},
        )
    ).one()


async def last_finished(s: Any, account_id: UUID) -> Any:
    """When research on the account last finished: a completed or partial run, or a done job."""
    return await s.scalar(
        text(
            "SELECT max(f) FROM (SELECT finished_at AS f FROM account_runs WHERE account_id = :a"
            " AND status IN ('COMPLETED', 'PARTIAL') UNION ALL SELECT finished_at FROM"
            " research_jobs"
            " WHERE account_id = :a AND status = 'done') x"
        ),
        {"a": account_id},
    )
