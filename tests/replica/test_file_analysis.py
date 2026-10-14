"""The File analysis screen's run lookup: the account's latest file-mode run (or the one asked
for), its state and file count; 404 for an account with none and for another client's account
(SYNTHETIC rows)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg
import pytest

from tests.conftest import ADMIN_DSN
from tests.replica.conftest import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _file_run(tenant: Any, domain: str, status: str) -> str:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        account = c.execute(
            "SELECT id FROM accounts WHERE tenant_id = %s AND domain = %s", (tenant, domain)
        ).fetchone()[0]
        finished = "now()" if status in ("COMPLETED", "PARTIAL") else "NULL"
        return str(
            c.execute(
                "INSERT INTO account_runs (tenant_id, account_id, status, graph_version,"
                " budget_json, reserved_credits, idempotency_key, budget_usd, input_mode,"
                f" finished_at) VALUES (%s, %s, %s, 'file', '{{}}', 0, %s, 1, 'file', {finished})"
                " RETURNING id",
                (tenant, account, status, uuid4().hex),
            ).fetchone()[0]
        )


async def test_the_latest_file_analysis_and_its_state(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    none = await _call(plane, "GET", "/v1/accounts/medica.example/file-analysis")
    assert none.status_code == 404
    first = _file_run(world["a"], "medica.example", "COMPLETED")
    running = _file_run(world["a"], "medica.example", "RUNNING")
    latest = (await _call(plane, "GET", "/v1/accounts/medica.example/file-analysis")).json()
    assert (latest["run_id"], latest["done"], latest["files"]) == (running, False, 0)
    asked = (
        await _call(plane, "GET", f"/v1/accounts/medica.example/file-analysis?run={first}")
    ).json()
    assert (asked["run_id"], asked["done"], asked["status"]) == (first, True, "COMPLETED")
    other = _plane(world["b"], "other-test", world["payloads"])
    assert (
        await _call(other, "GET", f"/v1/accounts/medica.example/file-analysis?run={first}")
    ).status_code == 404
