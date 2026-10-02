"""tests/integration/test_create_run.py - exercises POST /v1/runs (ARCH 18.8) against a real database, calling the
route function directly with a fabricated Identity and a stubbed event client."""
import asyncio
from unittest.mock import AsyncMock
from uuid import UUID

import psycopg
import pytest
from fastapi import HTTPException

import src.api.runs as runs
from src.api.auth import Identity
from tests.conftest import ADMIN_DSN

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def stub_events(monkeypatch):
    monkeypatch.setattr(runs, "inngest_client", type("C", (), {"send": AsyncMock()})())


def _balance(tenant: UUID) -> float:
    with psycopg.connect(ADMIN_DSN) as c:
        return float(c.execute("SELECT balance FROM wallets WHERE tenant_id = %s", (tenant,)).fetchone()[0])


def _set_balance(tenant: UUID, amount: float) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("UPDATE wallets SET balance = %s WHERE tenant_id = %s", (amount, tenant))


async def _create(tenant: UUID, domain: str, key: str):
    return await runs.create_run(runs.RunRequest(domain=domain), key, Identity("u1", tenant, "admin"))


async def test_create_run_reserves_credits_in_the_same_transaction(tenant_a: UUID):
    out = await _create(tenant_a, "acme.com", "idem-0001-aaaa")
    assert out["status"] == "QUEUED"
    assert _balance(tenant_a) == 94.0                          # 100 - 6 (standard)
    with psycopg.connect(ADMIN_DSN) as c:
        row = c.execute("SELECT entry, amount FROM wallet_ledger WHERE run_id = %s", (out["run_id"],)).fetchone()
    assert row[0] == "reserve" and float(row[1]) == -6.0


async def test_replay_with_the_same_key_returns_the_same_run_and_does_not_charge_twice(tenant_a: UUID):
    a = await _create(tenant_a, "acme.com", "idem-0002-aaaa")
    b = await _create(tenant_a, "acme.com", "idem-0002-aaaa")
    assert a["run_id"] == b["run_id"]
    assert _balance(tenant_a) == 94.0


@pytest.mark.xfail(strict=True, reason="defect 5: a replayed Idempotency-Key is not checked against the request body, "
                                       "so the same key with a different domain silently returns the first run.")
async def test_same_key_with_a_different_domain_is_rejected(tenant_a: UUID):
    await _create(tenant_a, "acme.com", "idem-0003-aaaa")
    with pytest.raises(HTTPException):
        await _create(tenant_a, "other.com", "idem-0003-aaaa")


async def test_insufficient_credits_returns_402_and_leaves_nothing_behind(tenant_a: UUID):
    _set_balance(tenant_a, 5)
    with pytest.raises(HTTPException) as e:
        await _create(tenant_a, "poor.com", "idem-0004-aaaa")
    assert e.value.status_code == 402
    assert _balance(tenant_a) == 5.0
    with psycopg.connect(ADMIN_DSN) as c:
        n = c.execute("SELECT count(*) FROM account_runs WHERE tenant_id = %s", (tenant_a,)).fetchone()[0]
        d = c.execute("SELECT count(*) FROM accounts WHERE tenant_id = %s", (tenant_a,)).fetchone()[0]
    assert n == 0 and d == 0                                   # the whole transaction rolled back


async def test_ten_parallel_runs_cannot_overspend_the_wallet(tenant_a: UUID):
    """ARCH 9.4: v1 let ten parallel requests each see a sufficient balance. With 30 credits and 6 per run,
    exactly five may succeed."""
    _set_balance(tenant_a, 30)
    res = await asyncio.gather(*[_create(tenant_a, f"d{i}.com", f"idem-par-{i:04d}") for i in range(10)],
                               return_exceptions=True)
    ok = [r for r in res if isinstance(r, dict)]
    denied = [r for r in res if isinstance(r, HTTPException) and r.status_code == 402]
    assert len(ok) == 5 and len(denied) == 5
    assert _balance(tenant_a) == 0.0
