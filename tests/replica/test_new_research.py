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


async def test_short_credits_and_a_full_cap_reserve_nothing(world: dict[str, Any]) -> None:
    plane = _plane(world["b"], "other-test", world["payloads"])
    est = (await _call(plane, "GET", "/v1/research/estimate")).json()
    _db(
        "UPDATE client_settings SET monthly_credit_pool = %s WHERE tenant_id = %s",
        est["credits"] - 1,
        world["b"],
    )
    body = {
        "request_id": str(uuid4()),
        "company_name": "Shortco",
        "domain": "shortco.example",
        "credits": est["credits"],
    }
    short = await _call(plane, "POST", "/v1/research", json=body)
    assert short.status_code == 409 and short.json()["type"].endswith("credits_short")
    assert _db("SELECT count(*) FROM accounts WHERE domain = 'shortco.example'") == [(0,)]
    _db(
        "UPDATE client_settings SET monthly_credit_pool = 100000, account_cap_per_user = 1"
        " WHERE tenant_id = %s",
        world["b"],
    )
    full = await _call(plane, "POST", "/v1/research", json={**body, "request_id": str(uuid4())})
    assert full.status_code == 409 and full.json()["type"].endswith("account_cap_reached")
