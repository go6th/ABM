"""Limits per user (owner decision 2026-10-14, Phase 4B section 5).

Two limits per user, set by the 6th admin (client defaults on the client, a user's own limits
when changed):

* **researches** (default 2): one full public research of one account: new research by company
  name, Refresh from web, or a scheduled refresh;
* **chats** (default 40): one question in Ask, or one file analysis.

Both reset on the 1st of each month, or never (the admin then raises limits by hand). A research
is counted when it starts and always finishes; the per-run dollar cap still applies. ``take``
checks and counts in one statement (``INSERT ... ON CONFLICT DO UPDATE ... WHERE used < limit``),
so two tabs can never both take the last one. A refused action names the limit in plain words,
and "Request more from admin" files an in-app request that the console approves or declines.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import text

Kind = Literal["researches", "chats"]
COLUMN = {"researches": "researches", "chats": "chats"}
LIMIT_COLUMN = {"researches": "research_limit", "chats": "chat_limit"}
DEFAULT_COLUMN = {"researches": "research_limit_default", "chats": "chat_limit_default"}


class LimitReached(Exception):
    """The user has used every research or chat of this period."""

    def __init__(self, kind: Kind, limit: int) -> None:
        super().__init__(kind)
        self.kind = kind
        self.limit = limit

    @property
    def message(self) -> str:
        """The refusal the user sees."""
        noun = (
            ("research" if self.limit == 1 else "researches")
            if self.kind == "researches"
            else ("chat" if self.limit == 1 else "chats")
        )
        return f"You have used your {self.limit} {noun}. Ask your admin to increase your limit."


@dataclass(frozen=True)
class UserLimits:
    """One user's limits now, and how they reset."""

    researches: int
    chats: int
    reset: str  # "monthly" or "never"

    def of(self, kind: Kind) -> int:
        """The limit of one kind."""
        return self.researches if kind == "researches" else self.chats


def period_of(now: datetime, reset: str) -> str:
    """The usage period: the calendar month, or "all" when limits never reset."""
    return "all" if reset == "never" else f"{now.year:04d}-{now.month:02d}"


def resets_on(now: datetime, reset: str) -> date | None:
    """When the counts start again (the 1st of next month), or None."""
    if reset == "never":
        return None
    return date(now.year + (now.month == 12), now.month % 12 + 1, 1)


async def limits_for(session: Any, user_id: str) -> UserLimits:
    """The user's limits (their own, else the client defaults), inside the tenant's txn."""
    from account_intelligence.replica.settings import client_settings

    await client_settings(session)  # the client's row exists
    row = (
        await session.execute(
            text(
                "SELECT cs.research_limit_default, cs.chat_limit_default, cs.limit_reset,"
                " ul.research_limit, ul.chat_limit FROM client_settings cs"
                " LEFT JOIN user_limits ul ON ul.tenant_id = cs.tenant_id"
                " AND ul.owner_user_id = :u"
            ),
            {"u": user_id},
        )
    ).one()
    return UserLimits(
        int(row.research_limit if row.research_limit is not None else row.research_limit_default),
        int(row.chat_limit if row.chat_limit is not None else row.chat_limit_default),
        str(row.limit_reset),
    )


async def used(session: Any, user_id: str, now: datetime, reset: str) -> tuple[int, int]:
    """Researches and chats used this period."""
    row = (
        await session.execute(
            text(
                "SELECT researches, chats FROM usage_periods"
                " WHERE owner_user_id = :u AND period = :p"
            ),
            {"u": user_id, "p": period_of(now, reset)},
        )
    ).first()
    return (int(row.researches), int(row.chats)) if row is not None else (0, 0)


async def check(session: Any, user_id: str, kind: Kind, now: datetime) -> UserLimits:
    """Refuse before anything starts when nothing is left (``LimitReached``); counts nothing."""
    lim = await limits_for(session, user_id)
    researches, chats = await used(session, user_id, now, lim.reset)
    if (researches if kind == "researches" else chats) >= lim.of(kind):
        raise LimitReached(kind, lim.of(kind))
    return lim


async def take(
    session: Any, user_id: str, kind: Kind, now: datetime, *, force: bool = False
) -> int:
    """Count one research or chat for the user in the caller's transaction and return how many
    are used now; ``LimitReached`` when none is left (atomic). ``force`` counts even past the
    limit (an answer that was already given when another tab took the last chat)."""
    lim = await limits_for(session, user_id)
    limit = lim.of(kind)
    if limit <= 0 and not force:
        raise LimitReached(kind, limit)
    col = COLUMN[kind]
    guard = "" if force else f" WHERE usage_periods.{col} < :limit"
    row = (
        await session.execute(
            text(
                f"INSERT INTO usage_periods (owner_user_id, period, {col}) VALUES (:u, :p, 1)"  # noqa: S608 fixed names
                f" ON CONFLICT (tenant_id, owner_user_id, period) DO UPDATE SET"
                f" {col} = usage_periods.{col} + 1, updated_at = now(){guard}"
                f" RETURNING {col}"
            ),
            {"u": user_id, "p": period_of(now, lim.reset), "limit": limit},
        )
    ).first()
    if row is None:
        raise LimitReached(kind, limit)
    return int(row[0])


async def view(session: Any, user_id: str, now: datetime) -> dict[str, Any]:
    """What the user sees: used, limit and left for both, when they reset, open requests."""
    lim = await limits_for(session, user_id)
    researches, chats = await used(session, user_id, now, lim.reset)
    pending = [
        str(r.kind)
        for r in await session.execute(
            text(
                "SELECT kind FROM limit_requests WHERE owner_user_id = :u AND status = 'pending'"
                " ORDER BY kind"
            ),
            {"u": user_id},
        )
    ]
    on = resets_on(now, lim.reset)
    return {
        "researches": {
            "used": researches,
            "limit": lim.researches,
            "left": max(0, lim.researches - researches),
        },
        "chats": {"used": chats, "limit": lim.chats, "left": max(0, lim.chats - chats)},
        "reset": lim.reset,
        "resets_on": on.isoformat() if on else None,
        "pending_requests": pending,
    }


async def request_more(
    session: Any, user_id: str, kind: Kind, note: str | None, now: datetime
) -> tuple[str, bool]:
    """File an in-app request for more (one open per user and limit); (request id, created)."""
    open_ = await session.scalar(
        text(
            "SELECT id FROM limit_requests WHERE owner_user_id = :u AND kind = :k"
            " AND status = 'pending'"
        ),
        {"u": user_id, "k": kind},
    )
    if open_ is not None:
        return str(open_), False
    rid = await session.scalar(
        text(
            "INSERT INTO limit_requests (owner_user_id, kind, note, created_at)"
            " VALUES (:u, :k, :n, :now) ON CONFLICT DO NOTHING RETURNING id"
        ),
        {"u": user_id, "k": kind, "n": (note or "").strip()[:500] or None, "now": now},
    )
    if rid is None:  # a concurrent request won
        rid = await session.scalar(
            text(
                "SELECT id FROM limit_requests WHERE owner_user_id = :u AND kind = :k"
                " AND status = 'pending'"
            ),
            {"u": user_id, "k": kind},
        )
    return str(rid), True


async def set_limits(
    session: Any,
    user_id: str,
    *,
    research_limit: int | None,
    chat_limit: int | None,
    actor: str,
    now: datetime,
) -> None:
    """The admin changes one user's limits (only the values given), as a system job."""
    await session.execute(
        text(
            "INSERT INTO user_limits (owner_user_id, research_limit, chat_limit, updated_by,"
            " updated_at) VALUES (:u, :r, :c, :by, :now) ON CONFLICT (tenant_id, owner_user_id)"
            " DO UPDATE SET research_limit = coalesce(EXCLUDED.research_limit,"
            " user_limits.research_limit), chat_limit = coalesce(EXCLUDED.chat_limit,"
            " user_limits.chat_limit), updated_by = EXCLUDED.updated_by,"
            " updated_at = EXCLUDED.updated_at"
        ),
        {"u": user_id, "r": research_limit, "c": chat_limit, "by": actor, "now": now},
    )


async def decide(
    session: Any,
    request_id: Any,
    *,
    approve: bool,
    research_limit: int | None,
    chat_limit: int | None,
    actor: str,
    now: datetime,
) -> dict[str, Any] | None:
    """Approve (with the new limits) or decline one pending request, tell the user in the app;
    None when it is not pending (already decided, or not this client's)."""
    row = (
        await session.execute(
            text(
                "UPDATE limit_requests SET status = :s, decided_at = :now, decided_by = :by,"
                " granted_research_limit = :r, granted_chat_limit = :c"
                " WHERE id = :i AND status = 'pending' RETURNING owner_user_id, kind"
            ),
            {
                "s": "approved" if approve else "declined",
                "now": now,
                "by": actor,
                "r": research_limit if approve else None,
                "c": chat_limit if approve else None,
                "i": request_id,
            },
        )
    ).first()
    if row is None:
        return None
    user = str(row.owner_user_id)
    if approve:
        await set_limits(
            session,
            user,
            research_limit=research_limit,
            chat_limit=chat_limit,
            actor=actor,
            now=now,
        )
    from account_intelligence.replica.notifications import notify

    await notify(
        session,
        user_id=user,
        kind="limit_request_approved" if approve else "limit_request_declined",
    )
    return {"user_id": user, "kind": str(row.kind)}
