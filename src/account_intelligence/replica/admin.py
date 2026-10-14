"""What the admin console (A1 to A7) shows about one client, read inside that client's own
transaction so row level security still scopes every query.

The operator routes loop over the clients and call these per client; nothing here reads more
than one tenant at a time. Usage is counts and categories only: question text, answer text and
file contents are never read here.

* ``client_row``: name, users (active of invited), accounts, this month's credits of the pool,
  last activity, the console status (Invited, Expiring, Near credit limit, Idle, Active), the days
  idle and until expiry, and the questions of the last 30 days with the share answered from the
  workspace.
* ``weekly`` and ``categories``: questions per week and what people ask about (from
  ``question_categories``).
* ``users``: each member's accounts, questions (30 days), last activity and access.
* ``runs`` and ``credits_by_purpose``: research jobs and the credits they used.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

from account_intelligence.replica.credits import period_of, pool
from account_intelligence.replica.settings import client_settings, client_status

NEAR_LIMIT = 0.85  # used and held at or above this share of the pool: "Near credit limit"
IDLE_AFTER_DAYS = 3  # no activity for this long: "Idle"
WARN_DAYS = 3  # access ends within this many days: "Expiring" (and the user is warned)
CATEGORY_LABEL = {
    "people": "People and owners",
    "hiring": "Hiring signals",
    "first_call": "First call prep",
    "technology": "Technology stack",
    "changes": "What changed",
    "other": "Other",
}
PURPOSE_LABEL = {
    "new_research": "New account research",
    "scheduled_refresh": "Scheduled refreshes",
    "web_refresh": "Web refreshes by users",
    "file_analysis": "File analysis",
}
KIND_LABEL = {
    "new_research": "New research",
    "scheduled_refresh": "Scheduled refresh",
    "web_refresh": "Web refresh",
    "file_analysis": "File analysis",
}
STATUS_LABEL = {
    "waiting_credit_limit": "Waiting: credit limit",
    "queued": "Queued",
    "running": "Running",
    "done": "Done",
    "failed": "Failed",
    "canceled": "Canceled",
}


def days_between(earlier: datetime, now: datetime) -> int:
    """Whole days from ``earlier`` to ``now`` (never negative)."""
    return max(0, (now - earlier).days)


def status_of(
    *,
    invited: bool,
    idle_days: int | None,
    expires_in: int | None,
    used: int,
    pool_size: int,
) -> str:
    """The console's status of one client, the most urgent first."""
    if invited and idle_days is None:
        return "Invited"
    if expires_in is not None and expires_in <= WARN_DAYS:
        return "Expiring"
    if pool_size and used >= NEAR_LIMIT * pool_size:
        return "Near credit limit"
    if idle_days is None or idle_days >= IDLE_AFTER_DAYS:
        return "Idle"
    return "Active"


async def client_row(s: Any, tenant_id: Any, now: datetime) -> dict[str, Any]:
    """One client's line in Clients (A2) and its summary in Overview (A1) and Client detail."""
    settings = await client_settings(s)
    status = await client_status(s)
    name = await s.scalar(text("SELECT name FROM tenants WHERE id = :t"), {"t": tenant_id})
    m = (
        await s.execute(
            text(
                "SELECT count(*) AS invited,"
                " count(*) FILTER (WHERE access_state = 'active' AND last_active_at >= :since)"
                " AS active,"
                " max(last_active_at) AS last_active"
                " FROM memberships WHERE status = 'active'"
            ),
            {"since": now - timedelta(days=30)},
        )
    ).one()
    accounts = int(await s.scalar(text("SELECT count(*) FROM accounts")) or 0)
    q = (
        await s.execute(
            text(
                "SELECT count(*) AS asked,"
                " count(*) FILTER (WHERE (answer_json->>'answered')::boolean) AS answered"
                " FROM chat_turns WHERE created_at >= :since AND status = 'completed'"
            ),
            {"since": now - timedelta(days=30)},
        )
    ).one()
    credits = await pool(s, now)
    idle_days = days_between(m.last_active, now) if m.last_active else None
    expires_in = max(0, settings.idle_expiry_days - idle_days) if idle_days is not None else None
    used = int(credits["used"]) + int(credits["held"])
    return {
        "tenant_id": str(tenant_id),
        "name": str(name),
        "users": int(m.invited),
        "active_users": int(m.active),
        "accounts": accounts,
        "credits_used": int(credits["used"]),
        "credits_held": int(credits["held"]),
        "credit_pool": int(credits["pool"]),
        "last_active_at": m.last_active.isoformat() if m.last_active else None,
        "idle_days": idle_days,
        "expires_in_days": expires_in,
        "questions": int(q.asked),
        "answered_locally": int(q.answered),
        "status": status_of(
            invited=status == "invited",
            idle_days=idle_days,
            expires_in=expires_in,
            used=used,
            pool_size=int(credits["pool"]),
        ),
    }


def week_start(day: date) -> date:
    """The Monday of ``day``'s week (question categories are kept per week)."""
    return day - timedelta(days=day.weekday())


def weekly_keys(now: datetime, weeks: int = 5) -> list[str]:
    """The ISO Monday dates of the last ``weeks`` weeks, oldest first."""
    first = week_start(now.date()) - timedelta(weeks=weeks - 1)
    return [(first + timedelta(weeks=i)).isoformat() for i in range(weeks)]


async def weekly(s: Any, now: datetime, weeks: int = 5) -> dict[str, int]:
    """Questions per week for the last ``weeks`` weeks, oldest first (ISO Monday dates)."""
    first = week_start(now.date()) - timedelta(weeks=weeks - 1)
    rows = await s.execute(
        text(
            "SELECT week, sum(questions) AS n FROM question_categories WHERE week >= :w"
            " GROUP BY week"
        ),
        {"w": first},
    )
    found = {r.week: int(r.n) for r in rows}
    return {
        (first + timedelta(weeks=i)).isoformat(): found.get(first + timedelta(weeks=i), 0)
        for i in range(weeks)
    }


async def categories(s: Any, now: datetime) -> dict[str, int]:
    """Questions by category over the last 30 days (counts only)."""
    rows = await s.execute(
        text(
            "SELECT category, sum(questions) AS n FROM question_categories WHERE week >= :w"
            " GROUP BY category"
        ),
        {"w": week_start((now - timedelta(days=30)).date())},
    )
    return {str(r.category): int(r.n) for r in rows}


async def users(s: Any, now: datetime) -> list[dict[str, Any]]:
    """Each member: email, accounts added, questions in 30 days, last activity and access."""
    settings = await client_settings(s)
    rows = await s.execute(
        text(
            "SELECT m.user_id, coalesce(u.email, u.display_name, m.user_id) AS email, m.role,"
            " m.access_state, m.last_active_at, m.ended_at,"
            " (SELECT count(*) FROM accounts a WHERE a.added_by = m.user_id) AS accounts,"
            " (SELECT count(*) FROM chat_turns t JOIN chat_threads h ON h.id = t.thread_id"
            "   WHERE h.owner_user_id = m.user_id AND t.created_at >= :since) AS questions"
            " FROM memberships m LEFT JOIN users u ON u.subject = m.user_id"
            " WHERE m.status = 'active' ORDER BY m.last_active_at DESC NULLS LAST, m.user_id"
        ),
        {"since": now - timedelta(days=30)},
    )
    out = []
    for r in rows:
        idle = days_between(r.last_active_at, now) if r.last_active_at else None
        out.append(
            {
                "user_id": str(r.user_id),
                "email": str(r.email),
                "role": str(r.role),
                "accounts": int(r.accounts),
                "account_cap": settings.account_cap_per_user,
                "questions": int(r.questions),
                "last_active_at": r.last_active_at.isoformat() if r.last_active_at else None,
                "access": str(r.access_state),
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "expires_in_days": max(0, settings.idle_expiry_days - idle)
                if idle is not None and r.access_state == "active"
                else None,
            }
        )
    return out


async def runs(s: Any, limit: int) -> list[dict[str, Any]]:
    """The newest research jobs (new research, refreshes, file analysis) with their credits."""
    rows = await s.execute(
        text(
            "SELECT j.id, coalesce(a.name, a.domain) AS account, j.kind, j.scope, j.status,"
            " CASE WHEN j.status IN ('done', 'failed', 'canceled') THEN j.credits_used"
            "   ELSE j.credits_estimated END AS credits,"
            " coalesce(j.started_at, j.created_at) AS started"
            " FROM research_jobs j JOIN accounts a ON a.id = j.account_id"
            " ORDER BY coalesce(j.started_at, j.created_at) DESC LIMIT :n"
        ),
        {"n": limit},
    )
    return [
        {
            "job_id": str(r.id),
            "account": str(r.account),
            "type": KIND_LABEL.get(r.kind, r.kind)
            + (f", {r.scope}" if r.kind == "web_refresh" and r.scope != "full" else ""),
            "credits": int(r.credits),
            "started_at": r.started.isoformat(),
            "status": STATUS_LABEL.get(r.status, r.status),
        }
        for r in rows
    ]


async def credits_by_purpose(s: Any, now: datetime) -> dict[str, int]:
    """This month's settled credits by what they were for."""
    rows = await s.execute(
        text(
            "SELECT purpose, sum(credits) AS n FROM credit_entries"
            " WHERE kind = 'settle' AND period = :p GROUP BY purpose"
        ),
        {"p": period_of(now)},
    )
    return {str(r.purpose): int(r.n) for r in rows}
