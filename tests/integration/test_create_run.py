"""Exercise POST /v1/runs against a real database with a stubbed event client."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import psycopg
import pytest
from starlette.requests import Request

from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.routes import runs
from tests.conftest import ADMIN_DSN

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def stub_events(monkeypatch):
    monkeypatch.setattr(runs, "inngest_client", type("C", (), {"send": AsyncMock()})())
    monkeypatch.setattr(
        runs,
        "get_settings",
        lambda: SimpleNamespace(
            run_credits_standard=6,
            run_credits_deep=18,
            run_budget_usd=5.0,
        ),
    )


def _balance(tenant: UUID) -> float:
    with psycopg.connect(ADMIN_DSN) as c:
        return float(
            c.execute("SELECT balance FROM wallets WHERE tenant_id = %s", (tenant,)).fetchone()[0]
        )


def _set_balance(tenant: UUID, amount: float) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("UPDATE wallets SET balance = %s WHERE tenant_id = %s", (amount, tenant))


async def _create(tenant: UUID, domain: str, key: str):
    app = SimpleNamespace(
        state=SimpleNamespace(
            domain_resolver=SimpleNamespace(
                resolve_public=AsyncMock(return_value=("93.184.216.34",))
            ),
            blocked_entity_port=SimpleNamespace(is_blocked=AsyncMock(return_value=False)),
        )
    )
    request = Request({"type": "http", "app": app})
    response = await runs.create_run(
        runs.RunRequest(domain=domain),
        request,
        key,
        Identity("u1", tenant, "admin"),
    )
    assert response.status_code == 202
    return json.loads(response.body)


async def test_create_run_reserves_credits_in_the_same_transaction(tenant_a: UUID):
    out = await _create(tenant_a, "acme.com", "idem-0001-aaaa")
    assert out["status"] == "QUEUED"
    assert _balance(tenant_a) == 94.0  # 100 - 6 (standard)
    with psycopg.connect(ADMIN_DSN) as c:
        row = c.execute(
            "SELECT entry, amount FROM wallet_ledger WHERE run_id = %s", (out["run_id"],)
        ).fetchone()
    assert row[0] == "reserve" and float(row[1]) == -6.0


async def test_replay_with_the_same_key_returns_the_same_run_and_does_not_charge_twice(
    tenant_a: UUID,
):
    a = await _create(tenant_a, "acme.com", "idem-0002-aaaa")
    b = await _create(tenant_a, "acme.com", "idem-0002-aaaa")
    assert a["run_id"] == b["run_id"]
    assert _balance(tenant_a) == 94.0


async def test_same_key_with_a_different_domain_is_rejected(tenant_a: UUID):
    await _create(tenant_a, "acme.com", "idem-0003-aaaa")
    with pytest.raises(APIProblem) as error:
        await _create(tenant_a, "other.com", "idem-0003-aaaa")
    assert error.value.status == 409


async def test_insufficient_credits_returns_402_and_leaves_nothing_behind(tenant_a: UUID):
    _set_balance(tenant_a, 5)
    with pytest.raises(APIProblem) as e:
        await _create(tenant_a, "poor.com", "idem-0004-aaaa")
    assert e.value.status == 402
    assert _balance(tenant_a) == 5.0
    with psycopg.connect(ADMIN_DSN) as c:
        n = c.execute(
            "SELECT count(*) FROM account_runs WHERE tenant_id = %s", (tenant_a,)
        ).fetchone()[0]
        d = c.execute("SELECT count(*) FROM accounts WHERE tenant_id = %s", (tenant_a,)).fetchone()[
            0
        ]
    assert n == 0 and d == 0  # the whole transaction rolled back


async def test_ten_parallel_runs_cannot_overspend_the_wallet(tenant_a: UUID):
    """Ten concurrent runs against 30 credits allow exactly five standard reservations."""
    _set_balance(tenant_a, 30)
    res = await asyncio.gather(
        *[_create(tenant_a, f"d{i}.com", f"idem-par-{i:04d}") for i in range(10)],
        return_exceptions=True,
    )
    ok = [r for r in res if isinstance(r, dict)]
    denied = [r for r in res if isinstance(r, APIProblem) and r.status == 402]
    assert len(ok) == 5 and len(denied) == 5
    assert _balance(tenant_a) == 0.0
