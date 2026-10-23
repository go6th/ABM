"""New research on a company, on the credit pool: the estimate first, then Confirm reserves and
starts it; a moved estimate, short credits or a full account cap reserve nothing (SYNTHETIC).

GUARANTEE(test_new_research_shows_the_estimate_and_starts_only_on_confirm)
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg
import pytest

from tests.conftest import ADMIN_DSN
from tests.replica.conftest import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


async def test_new_research_shows_the_estimate_and_starts_only_on_confirm(
    world: dict[str, Any],
) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    est = (await _call(plane, "GET", "/v1/research/estimate")).json()
    assert est["credits"] > 0 and est["minutes"] > 0 and est["enough"] is True
    assert est["accounts_used"] == 4 and est["account_cap"] == 34
    # what it is expected to use, before anything is confirmed (owner decision 2026-10-21)
    for key in ("usage", "file_usage", "file_public_usage"):
        assert est[key]["tokens"] > 0 and est[key]["cost_usd"] > 0 and est[key]["runs"] >= 0
    assert _db("SELECT count(*) FROM accounts WHERE domain = 'newco.example'") == [(0,)]

    request = str(uuid4())
    body = {
        "request_id": request,
        "company_name": "Newco",
        "domain": "newco.example",
        "credits": est["credits"],
    }
    moved = await _call(plane, "POST", "/v1/research", json={**body, "credits": 1})
    assert moved.status_code == 409 and moved.json()["type"].endswith("estimate_changed")
    assert _db("SELECT count(*) FROM accounts WHERE domain = 'newco.example'") == [(0,)]

    r = await _call(plane, "POST", "/v1/research", json=body)
    assert r.status_code == 201, r.text
    started = r.json()
    assert started["status"] == "queued" and started["credits"] == est["credits"]
    job = _db(
        "SELECT j.kind, j.scope, j.status, a.added_by, a.name FROM research_jobs j"
        " JOIN accounts a ON a.id = j.account_id WHERE j.id = %s",
        started["job_id"],
    )
    assert job == [("new_research", "full", "queued", "priya-test", "Newco")]
    again = await _call(plane, "POST", "/v1/research", json=body)  # a double click
    assert again.json()["job_id"] == started["job_id"]
    assert _db("SELECT count(*) FROM credit_entries WHERE job_id = %s", started["job_id"]) == [(1,)]


async def test_no_research_left_reserves_nothing(world: dict[str, Any]) -> None:
    """Owner decision 2026-10-14: a user's research limit, not credits or an account cap, decides
    whether new research starts; with none left nothing is created or reserved."""
    user = f"limited-{uuid4().hex[:6]}"
    plane = _plane(world["b"], user, world["payloads"])
    _db(
        "INSERT INTO user_limits (tenant_id, owner_user_id, research_limit, updated_by)"
        " VALUES (%s, %s, 0, 'test')",
        world["b"],
        user,
    )
    body = {"request_id": str(uuid4()), "company_name": "Shortco", "domain": "shortco.example"}
    refused = await _call(plane, "POST", "/v1/research", json=body)
    assert refused.status_code == 403, refused.text
    assert refused.json()["type"].endswith("limit_reached")
    assert refused.json()["detail"] == (
        "You have used your 0 researches. Ask your admin to increase your limit."
    )
    assert _db("SELECT count(*) FROM accounts WHERE domain = 'shortco.example'") == [(0,)]
    # a tiny credit pool no longer stops research: credits are recorded, not gating
    _db("UPDATE user_limits SET research_limit = 1 WHERE owner_user_id = %s", user)
    _db("UPDATE client_settings SET monthly_credit_pool = 1 WHERE tenant_id = %s", world["b"])
    try:
        ok = await _call(plane, "POST", "/v1/research", json={**body, "request_id": str(uuid4())})
        assert ok.status_code == 201, ok.text
    finally:
        _db(
            "UPDATE client_settings SET monthly_credit_pool = 3000 WHERE tenant_id = %s",
            world["b"],
        )


async def test_stop_ends_a_research_that_has_not_started_and_releases_its_credits(
    world: dict[str, Any],
) -> None:
    """Stop on the account page: a research waiting or queued (no run yet) ends canceled, its
    reserved credits go back to the pool, and a second Stop says it has ended."""
    plane = _plane(world["a"], "priya-test", world["payloads"])
    body = {"request_id": str(uuid4()), "company_name": "Stopco", "domain": "stopco.example"}
    started = (await _call(plane, "POST", "/v1/research", json=body)).json()
    job = started["job_id"]
    assert started["status"] == "queued"
    stopped = await _call(plane, "POST", f"/v1/research/{job}/stop")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json() == {"job_id": job, "status": "canceled"}
    assert _db("SELECT status, credits_used FROM research_jobs WHERE id = %s", job) == [
        ("canceled", 0)
    ]
    held = _db(
        "SELECT sum(credits) FILTER (WHERE kind = 'reserve') - sum(credits) FILTER"
        " (WHERE kind = 'release') FROM credit_entries WHERE job_id = %s",
        job,
    )
    assert held == [(0,)]
    again = await _call(plane, "POST", f"/v1/research/{job}/stop")
    assert again.status_code == 409 and again.json()["type"].endswith("research_ended")
    ws = (await _call(plane, "GET", "/v1/accounts/stopco.example/workspace")).json()
    assert ws["open_job"] is None


async def test_a_research_goal_is_optional_stored_and_checked(world: dict[str, Any]) -> None:
    """Owner decision 2026-10-23: New research takes an optional goal in the user's words
    (whitespace collapsed, 3 to 500 characters, empty is none); the body still refuses fields it
    does not know."""
    plane = _plane(world["a"], f"goal-{uuid4().hex[:6]}", world["payloads"])

    def body(**extra: object) -> dict[str, object]:
        domain = f"goal-{uuid4().hex[:6]}.example"
        return {"request_id": str(uuid4()), "company_name": "Goalco", "domain": domain, **extra}

    for bad in ("ab", "  a \n ", "x" * 501):
        refused = await _call(plane, "POST", "/v1/research", json=body(goal=bad))
        assert refused.status_code == 422, (bad, refused.text)
    unknown = await _call(plane, "POST", "/v1/research", json=body(aim="anything"))
    assert unknown.status_code == 422
    r = await _call(
        plane, "POST", "/v1/research", json=body(goal="  we sell payroll:\n their HR stack ")
    )
    assert r.status_code == 201, r.text
    assert _db("SELECT goal FROM research_jobs WHERE id = %s", r.json()["job_id"]) == [
        ("we sell payroll: their HR stack",)
    ]
    blank = await _call(plane, "POST", "/v1/research", json=body(goal="   "))
    assert blank.status_code == 201, blank.text
    assert _db("SELECT goal FROM research_jobs WHERE id = %s", blank.json()["job_id"]) == [(None,)]
