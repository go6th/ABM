"""Limits per user through the real routes (owner decision 2026-10-14, Phase 4B section 5):
researches and chats are counted when they start, refused in plain words when used up, counted
once per request, never both taken by two tabs, and raised by the 6th admin through an in-app
request that the console approves or declines."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import limits as LIM
from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import make_app
from tests.replica.test_admin_console import _staff
from tests.replica.test_admin_console import staff_env as staff_env_fixture
from tests.replica.test_workspace_api import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
staff_env = staff_env_fixture  # the console tests' staff settings


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _limit(tenant: UUID, user: str, researches: int, chats: int) -> None:
    _db(
        "INSERT INTO user_limits (tenant_id, owner_user_id, research_limit, chat_limit,"
        " updated_by) VALUES (%s, %s, %s, %s, 'test')",
        tenant,
        user,
        researches,
        chats,
    )


def _account(tenant: UUID, user: str, domain: str) -> None:
    _db(
        "INSERT INTO accounts (tenant_id, owner_user_id, added_by, domain, name)"
        " VALUES (%s, %s, %s, %s, %s)",
        tenant,
        user,
        user,
        domain,
        domain.split(".")[0],
    )


async def _refresh(plane: Any, domain: str, request_id: str | None = None) -> Any:
    return await _call(
        plane,
        "POST",
        f"/v1/accounts/{domain}/refresh",
        json={"request_id": request_id or str(uuid4()), "scope": "jobs"},
    )


async def test_researches_are_counted_refused_in_plain_words_and_counted_once(
    world: dict[str, Any],
) -> None:
    user = f"lim-{uuid4().hex[:6]}"
    plane = _plane(world["a"], user, world["payloads"])
    _limit(world["a"], user, 2, 40)
    for name in ("one", "two", "three"):
        _account(world["a"], user, f"lim-{name}-{user}.example")
    first_key = str(uuid4())
    assert (await _refresh(plane, f"lim-one-{user}.example", first_key)).status_code == 201
    # a retried confirm (same request) is the same research: nothing more is counted
    assert (await _refresh(plane, f"lim-one-{user}.example", first_key)).status_code == 201
    assert (await _refresh(plane, f"lim-two-{user}.example")).status_code == 201
    mine = (await _call(plane, "GET", "/v1/me/limits")).json()
    assert mine["researches"] == {"used": 2, "limit": 2, "left": 0}
    assert mine["reset"] == "monthly" and mine["resets_on"].endswith("-01")
    refused = await _refresh(plane, f"lim-three-{user}.example")
    assert refused.status_code == 403
    assert refused.json()["detail"] == (
        "You have used your 2 researches. Ask your admin to increase your limit."
    )
    assert refused.json()["context"] == {"limit": "researches", "allowed": 2}


async def test_a_question_is_refused_before_it_starts_when_no_chat_is_left(
    world: dict[str, Any],
) -> None:
    user = f"chat-{uuid4().hex[:6]}"
    plane = _plane(world["a"], user, world["payloads"])
    _limit(world["a"], user, 2, 1)
    ask = {"question": "What changed this week?"}
    first = await _call(plane, "POST", "/v1/chat/ask", json={**ask, "request_id": str(uuid4())})
    assert first.status_code == 200 and "event: answer" in first.text
    assert (await _call(plane, "GET", "/v1/me/limits")).json()["chats"]["used"] == 1
    second = await _call(plane, "POST", "/v1/chat/ask", json={**ask, "request_id": str(uuid4())})
    assert second.status_code == 403
    assert "You have used your 1 chat." in second.json()["detail"]


async def test_two_tabs_can_never_both_take_the_last_research(world: dict[str, Any]) -> None:
    user = f"race-{uuid4().hex[:6]}"
    _limit(world["a"], user, 1, 40)
    now = datetime.now(UTC)

    async def take() -> str:
        try:
            async with tenant_txn(world["a"], user=user, system=False) as s:
                await LIM.take(s, user, "researches", now)
            return "taken"
        except LIM.LimitReached:
            return "refused"

    assert sorted(await asyncio.gather(take(), take())) == ["refused", "taken"]


async def test_limits_that_never_reset_count_for_all_time() -> None:
    assert LIM.period_of(datetime(2026, 10, 14, tzinfo=UTC), "never") == "all"
    assert LIM.period_of(datetime(2026, 12, 31, tzinfo=UTC), "monthly") == "2026-12"
    assert LIM.resets_on(datetime(2026, 12, 31, tzinfo=UTC), "monthly").isoformat() == "2027-01-01"
    assert LIM.resets_on(datetime(2026, 12, 31, tzinfo=UTC), "never") is None


async def test_a_request_for_more_is_approved_in_the_console_and_the_user_told(
    world: dict[str, Any],
    staff_env: None,  # noqa: F811
) -> None:
    user = f"ask-{uuid4().hex[:6]}"
    plane = _plane(world["a"], user, world["payloads"])
    _limit(world["a"], user, 0, 40)
    sent = await _call(
        plane, "POST", "/v1/limits/requests", json={"kind": "researches", "note": "Q4 pipeline"}
    )
    assert sent.status_code == 201 and sent.json()["created"] is True
    again = await _call(plane, "POST", "/v1/limits/requests", json={"kind": "researches"})
    assert again.json() == {**sent.json(), "created": False}
    assert (await _call(plane, "GET", "/v1/me/limits")).json()["pending_requests"] == ["researches"]

    staff = make_app()
    listed = (await _staff(staff, "GET", "/operator/v1/console/requests")).json()["requests"]
    mine = next(r for r in listed if r["request_id"] == sent.json()["request_id"])
    assert (mine["status"], mine["kind"], mine["note"], mine["research_limit"]) == (
        "pending",
        "researches",
        "Q4 pipeline",
        0,
    )
    path = f"/operator/v1/console/requests/{world['a']}/{mine['request_id']}"
    assert (await _staff(staff, "POST", f"{path}/approve", json={})).status_code == 422
    ok = await _staff(staff, "POST", f"{path}/approve", json={"research_limit": 5})
    assert ok.status_code == 200, ok.text
    assert (await _staff(staff, "POST", f"{path}/decline")).status_code == 409  # closed
    after = (await _call(plane, "GET", "/v1/me/limits")).json()
    assert after["researches"]["limit"] == 5 and after["pending_requests"] == []
    assert _db(
        "SELECT kind FROM notifications WHERE tenant_id = %s AND user_id = %s",
        world["a"],
        user,
    ) == [("limit_request_approved",)]
    assert _db(
        "SELECT count(*) FROM audit_events WHERE tenant_id = %s AND action LIKE %s",
        world["a"],
        "%limit_request.approve%",
    ) == [(1,)]


async def test_the_admin_changes_a_users_limits_and_sees_their_use(
    world: dict[str, Any],
    staff_env: None,  # noqa: F811
) -> None:
    user = f"chg-{uuid4().hex[:6]}"
    _plane(world["a"], user, world["payloads"])  # makes the membership
    staff = make_app()
    changed = await _staff(
        staff,
        "PUT",
        f"/operator/v1/console/users/{world['a']}/limits",
        json={"user_id": user, "chat_limit": 7},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["chat_limit"] == 7
    users = (await _staff(staff, "GET", "/operator/v1/console/users")).json()["users"]
    row = next(u for u in users if u["user_id"] == user)
    assert (row["chat_limit"], row["chats_used"]) == (7, 0)
