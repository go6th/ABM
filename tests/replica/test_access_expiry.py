"""Idle expiry on PostgreSQL (SYNTHETIC client): with 7 idle days, a user idle 5 days is warned
once, a user idle 8 days loses access (refused by the active-role check every sign-in method and
API call uses, signed out, history kept), a recent user and one who never signed in are left
alone; Re-enable restores access and expiry counts again from then.

GUARANTEE(test_idle_users_are_warned_then_expired_and_history_is_kept)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import access
from identity import membership
from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import add_member, new_tenant

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


async def test_idle_users_are_warned_then_expired_and_history_is_kept() -> None:
    now = datetime.now(UTC)
    t = new_tenant("idle-expiry")
    for user, days in (("recent", 1), ("soon", 5), ("gone", 8), ("never", None)):
        add_member(t, user, "analyst")
        if days is not None:
            _db(
                "UPDATE memberships SET last_active_at = %s WHERE tenant_id = %s AND user_id = %s",
                now - timedelta(days=days),
                t,
                user,
            )
    async with tenant_txn(t) as s:
        assert await access.expire_idle(s, now) == (1, 1)
    async with tenant_txn(t) as s:
        assert await access.expire_idle(s, now) == (0, 0)  # warned once; gone stays gone
        assert await membership.active_role(s, "gone") is None
        assert await membership.active_role(s, "never") == "analyst"
        assert await membership.active_role(s, "recent") == "analyst"
    assert _db("SELECT user_id, kind FROM notifications WHERE tenant_id = %s", t) == [
        ("soon", "access_expiring")
    ]
    assert _db(
        "SELECT access_state, ended_at IS NOT NULL FROM memberships WHERE tenant_id = %s"
        " AND user_id = 'gone'",
        t,
    ) == [("expired", True)]
    assert _db(
        "SELECT event, reason, actor FROM membership_events WHERE tenant_id = %s"
        " AND user_id = 'gone'",
        t,
    ) == [("suspended", "expired", access.ACTOR)]
    async with tenant_txn(t) as s:
        assert await membership.reenable(s, "gone", "op-1", now)
    async with tenant_txn(t) as s:
        assert await membership.active_role(s, "gone") == "analyst"
        assert await access.expire_idle(s, now) == (0, 0)  # counts again from the re-enable


async def test_one_failing_client_never_stops_the_pass() -> None:
    from contextlib import asynccontextmanager

    from account_intelligence.db.session import untenanted_session

    t = new_tenant("idle-pass")
    calls: list[Any] = []

    @asynccontextmanager
    async def txn(tid: Any):  # type: ignore[no-untyped-def]
        calls.append(tid)
        if tid == t:
            raise RuntimeError("this client is unreachable")
        async with tenant_txn(tid) as s:
            yield s

    report = await access.expire_everywhere(
        untenanted_session, txn, datetime(2000, 1, 1, tzinfo=UTC)
    )
    assert str(t) in report.failed and report.clients == len(calls) - len(report.failed)
