"""SD-02: a hard dollar ceiling per Ask conversation that no switch lifts.

``config/chat_models.yaml`` allowed $1,000 per thread and nothing enforced it. A question on a
thread whose answered turns already cost ``chat.store.HARD_THREAD_CEILING_USD`` ($25, owner
decision 2026-11-10) or more is now refused before any model is called, with the caps switched
off too. A new conversation still answers. A thread can pass the ceiling by at most the one turn
that crossed it (the check runs before each turn).

GUARANTEE(test_a_question_on_a_thread_at_the_ceiling_is_refused_with_the_switches_off)
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from account_intelligence.chat.store import HARD_THREAD_CEILING_USD
from tests.conftest import ADMIN_DSN
from tests.replica.test_workspace_api import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _thread_of(stream: str) -> str:
    for block in stream.split("\n\n"):
        if block.startswith("event: thread"):
            data = block.split("data: ", 1)[1]
            return str(json.loads(data)["thread_id"])
    raise AssertionError(f"no thread frame in {stream[:200]!r}")


async def test_a_question_on_a_thread_at_the_ceiling_is_refused_with_the_switches_off(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RESEARCH_CAPS", "off")
    monkeypatch.setenv("TOKEN_GUARD", "off")
    assert Decimal("25.00") == HARD_THREAD_CEILING_USD
    user = f"ceil-{uuid4().hex[:6]}"
    plane = _plane(world["a"], user, world["payloads"])
    ask = {"question": "What changed this week?"}
    first = await _call(plane, "POST", "/v1/chat/ask", json={**ask, "request_id": str(uuid4())})
    assert first.status_code == 200 and "event: answer" in first.text
    thread = _thread_of(first.text)
    # just under the ceiling: the next question is still answered
    _db("UPDATE chat_turns SET cost_usd = 24.99 WHERE thread_id = %s", thread)
    on = {**ask, "thread_id": thread}
    second = await _call(plane, "POST", "/v1/chat/ask", json={**on, "request_id": str(uuid4())})
    assert second.status_code == 200 and "event: answer" in second.text
    # the answered turns now cost the ceiling: refused before any model is called
    _db(
        "UPDATE chat_turns SET cost_usd = 12.50 WHERE thread_id = %s",
        thread,
    )
    turns_before = _db("SELECT count(*) FROM chat_turns WHERE thread_id = %s", thread)
    third = await _call(plane, "POST", "/v1/chat/ask", json={**on, "request_id": str(uuid4())})
    assert third.status_code == 409, third.text
    problem = third.json()
    assert problem["type"].endswith("thread_spend_ceiling") or problem.get("code") == (
        "thread_spend_ceiling"
    ), problem
    assert "new conversation" in problem["detail"]
    assert _db("SELECT count(*) FROM chat_turns WHERE thread_id = %s", thread) == turns_before
    # a new conversation still answers
    fresh = await _call(plane, "POST", "/v1/chat/ask", json={**ask, "request_id": str(uuid4())})
    assert fresh.status_code == 200 and "event: answer" in fresh.text
