"""The nightly isolation check on PostgreSQL and the real application (SYNTHETIC): a pass covers
the structure, every tenant table and the read routes from the dedicated probe client, records
itself and shows in the console; a table without forced row level security that shows another
client's rows, and a route that shows the canary, are both caught and recorded as a failed pass.

GUARANTEE(test_the_nightly_isolation_check_records_a_pass_and_catches_a_leak)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn, untenanted_session
from account_intelligence.replica import isolation
from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import make_app, new_tenant

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


async def test_the_nightly_isolation_check_records_a_pass_and_catches_a_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_app().app
    now = datetime.now(UTC)
    new_tenant("isolation-real-client")
    done = await isolation.run_check(app, untenanted_session, tenant_txn, now)
    assert done.passed, done.failures
    assert done.tables > 50 and done.routes > 10 and done.clients >= 1
    async with untenanted_session() as plain:
        assert (await isolation.latest(plain))["status"] == "passed"
        pair = await isolation.check_clients(plain)
    again = await isolation.run_check(app, untenanted_session, tenant_txn, now)
    async with untenanted_session() as plain:
        assert await isolation.check_clients(plain) == pair  # one pair, made once
    assert again.passed

    # a leak: a tenant table without row level security that holds another client's row
    probe, target, canary = await isolation.ensure_pair(untenanted_session, tenant_txn, now)
    _db("CREATE TABLE leak_probe (tenant_id uuid NOT NULL)")
    try:
        _db("GRANT SELECT ON leak_probe TO app_rw")
        _db("INSERT INTO leak_probe VALUES (%s)", target)
        from account_intelligence.api.policy import ROUTE_POLICIES, _tenant

        async def leaky() -> dict[str, str]:
            return {"name": canary}

        app.add_api_route("/v1/leaky", leaky, methods=["GET"])
        monkeypatch.setitem(ROUTE_POLICIES, ("GET", "/v1/leaky"), _tenant({"analyst"}, "read"))
        later = now + timedelta(seconds=1)
        failed = await isolation.run_check(app, untenanted_session, tenant_txn, later)
    finally:
        _db("DROP TABLE leak_probe")
    assert not failed.passed
    assert "no forced RLS: leak_probe" in failed.failures
    assert "table leak_probe shows another client's rows" in failed.failures
    assert "route /v1/leaky shows the canary client's data" in failed.failures
    row = _db("SELECT passed, failures, detail FROM isolation_checks ORDER BY ran_at DESC LIMIT 1")
    assert row[0][0] is False and row[0][1] == 3 and "leak_probe" in row[0][2]
    assert str(target) not in row[0][2] and canary not in row[0][2]  # names only
