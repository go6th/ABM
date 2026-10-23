"""The operator console's run detail and client suspension on PostgreSQL (SYNTHETIC clients): a run
opens with its status and token card (counts and dollars only); a run of another client is not
found; the client detail says when the client is suspended, and Reactivate lifts it.

GUARANTEE(test_a_run_opens_with_its_token_card)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from tests.conftest import ADMIN_DSN
from tests.fakes.phase3 import FakeStaffIdp
from tests.phase3.conftest import BASE, make_app

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

OP = {"Authorization": "Bearer operator:op-console"}


@pytest.fixture(autouse=True)
def staff_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in (
        ("STAFF_JWT_ISSUER", "https://staff.example/"),
        ("STAFF_JWT_AUDIENCE", "staff"),
        ("STAFF_JWKS_URL", "https://staff.example/jwks"),
        ("STAFF_OPERATOR_EMAILS", "admin@6th.example"),
        ("STAFF_SUPPORT_EMAILS", "dev@6th.example"),
    ):
        monkeypatch.setenv(k, v)


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _run(tenant: UUID) -> UUID:
    (account,) = _db(
        "INSERT INTO accounts (tenant_id, owner_user_id, added_by, domain, name)"
        " VALUES (%s, 'op-test', 'op-test', %s, 'Console Co') RETURNING id",
        tenant,
        f"console-{uuid4().hex[:8]}.example",
    )[0]
    (run,) = _db(
        "INSERT INTO account_runs (tenant_id, account_id, owner_user_id, status, graph_version,"
        " budget_json, reserved_credits, idempotency_key, budget_usd, finished_at,"
        " finished_reason) VALUES (%s, %s, 'op-test', 'COMPLETED', 'test', '{}', 0, %s, 5,"
        " now(), 'completed') RETURNING id",
        tenant,
        account,
        uuid4().hex,
    )[0]
    return UUID(str(run))


async def _staff(plane: Any, method: str, path: str, **kw: Any) -> httpx.Response:
    plane.app.state.container.tenant_plane.staff_verifier = FakeStaffIdp(
        now=plane.app.state.container.clock.now()
    )
    transport = httpx.ASGITransport(app=plane.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        return await c.request(method, path, headers=OP, **kw)


async def _client(plane: Any, name: str) -> UUID:
    made = await _staff(
        plane,
        "POST",
        "/operator/v1/console/clients",
        json={"name": name, "admin_email": f"lead-{uuid4().hex[:8]}@console.example"},
    )
    assert made.status_code == 201, made.text
    return UUID(made.json()["tenant_id"])


async def test_a_run_opens_with_its_token_card() -> None:
    plane = make_app()
    tenant = await _client(plane, "Console Detail")
    run = _run(tenant)
    got = await _staff(plane, "GET", f"/operator/v1/console/runs/{tenant}/{run}")
    assert got.status_code == 200, got.text
    body = got.json()
    assert (body["run_id"], body["status"], body["client"]) == (
        str(run),
        "COMPLETED",
        "Console Detail",
    )
    assert body["token_use"]["total"] == 0 and "cost_usd" in body["token_use"]

    listed = (await _staff(plane, "GET", "/operator/v1/console/runs")).json()["runs"]
    assert all("run_id" in r for r in listed)

    other = await _client(plane, "Console Other")
    missing = await _staff(plane, "GET", f"/operator/v1/console/runs/{other}/{run}")
    assert missing.status_code == 404, missing.text


async def test_client_detail_shows_a_suspension_until_reactivated() -> None:
    plane = make_app()
    tenant = await _client(plane, "Console Suspend")
    detail = (await _staff(plane, "GET", f"/operator/v1/console/clients/{tenant}")).json()
    assert detail["suspended"] is False
    done = await _staff(
        plane,
        "POST",
        f"/operator/v1/tenants/{tenant}/suspend",
        json={"reason_code": "client_request"},
    )
    assert done.status_code == 200, done.text
    detail = (await _staff(plane, "GET", f"/operator/v1/console/clients/{tenant}")).json()
    assert detail["suspended"] is True
    back = await _staff(
        plane,
        "POST",
        f"/operator/v1/tenants/{tenant}/reactivate",
        json={"reason_code": "client_request"},
    )
    assert back.status_code == 200, back.text
    detail = (await _staff(plane, "GET", f"/operator/v1/console/clients/{tenant}")).json()
    assert detail["suspended"] is False
