"""The account cap per user is enforced on every way an account is added: adding it, starting
research on a new company, a file analysis that names a new company, and runs created through
the lifecycle (SYNTHETIC tenants and domains).

GUARANTEE(test_every_way_of_adding_an_account_respects_the_cap)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.replica.settings import AccountCapReached
from account_intelligence.runs import lifecycle
from tests.conftest import ADMIN_DSN
from tests.replica.conftest import _call, _plane
from tests.runs.test_lifecycle_db import LIMITS, req, tenant
from tests.tracker.world import tracker

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _cap(t: UUID, cap: int) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "UPDATE client_settings SET account_cap_per_user = %s WHERE tenant_id = %s", (cap, t)
        )


async def test_every_way_of_adding_an_account_respects_the_cap() -> None:
    t = tenant()
    _cap(t, 2)
    async with tenant_txn(t) as s:
        await lifecycle.add_account(s, t, "one.example", added_by="u1")
    async with tenant_txn(t) as s:  # a run on a new company is an add
        await lifecycle.create_run(
            s,
            t,
            req(domain="two.example"),
            LIMITS,
            tracker(s),
            lifecycle.CreditJob(actor="u1", credits=6),
        )
    with pytest.raises(AccountCapReached) as err:
        async with tenant_txn(t) as s:
            await lifecycle.add_account(s, t, "three.example", added_by="u1")
    assert err.value.message == (
        "You have used all 2 accounts. Ask your administrator to raise the cap."
    )
    with pytest.raises(AccountCapReached):
        async with tenant_txn(t) as s:
            await lifecycle.create_run(
                s,
                t,
                req(domain="four.example"),
                LIMITS,
                tracker(s),
                lifecycle.CreditJob(actor="u1", credits=6),
            )
    async with tenant_txn(t) as s:  # an account already in the workspace is no new add
        _, created = await lifecycle.add_account(s, t, "one.example", added_by="u1")
        assert not created
    async with tenant_txn(t) as s:  # another person has their own places
        await lifecycle.add_account(s, t, "five.example", added_by="u2")
    async with tenant_txn(t) as s:  # the scheduler and the system count against nobody
        await lifecycle.add_account(s, t, "six.example")
    rows = dict(
        psycopg.connect(ADMIN_DSN)
        .execute("SELECT domain, added_by FROM accounts WHERE tenant_id = %s ORDER BY domain", (t,))
        .fetchall()
    )
    assert rows == {
        "five.example": "u2",
        "one.example": "u1",
        "six.example": None,
        "two.example": "u1",
    }


async def test_the_api_refuses_an_add_past_the_cap_with_the_designs_words(
    world: dict[str, Any],
) -> None:
    _cap(world["b"], 1)  # other-test already added one account
    plane = _plane(world["b"], "other-test", world["payloads"])
    r = await _call(plane, "POST", "/v1/accounts", json={"domain": "new-company.example"})
    assert r.status_code == 409
    body = r.json()
    assert body["type"] == "urn:6th:problem:account_cap_reached"
    assert (
        body["detail"] == "You have used all 1 accounts. Ask your administrator to raise the cap."
    )
