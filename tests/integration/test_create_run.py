"""Exercise POST /v1/runs against a real database with the test container (in-process scheduler).

The route now creates runs through ``runs.lifecycle.create_run`` and enqueues on the
``RunScheduler`` (Inngest retired, conflict row C11). Credits come from the client's monthly
pool (the single ledger, migration 0057): a new company reserves the rate table's estimate."""

import asyncio
from types import SimpleNamespace
from uuid import UUID

import psycopg
import pytest
from fastapi import HTTPException

from account_intelligence.api.auth import Identity
from account_intelligence.api.routes import runs
from tests.conftest import ADMIN_DSN

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def container():
    from account_intelligence.app.container import build_container

    c = build_container({"APP_ENV": "test"})
    REQUEST.app.state.container = c
    return c


# the route function called directly: the request carries what the router layer would have set
REQUEST = SimpleNamespace(
    app=SimpleNamespace(state=SimpleNamespace(container=None)),
    state=SimpleNamespace(principal=None, request_id=None),
    scope={"route": None},
    url=SimpleNamespace(path="/v1/runs"),
    method="POST",
    headers={},
)


def _estimate() -> int:
    from account_intelligence.replica.credits import rates

    return rates().estimates["new_research"].credits


def _balance(tenant: UUID) -> float:
    """Credits left in the client's pool this month."""
    with psycopg.connect(ADMIN_DSN) as c:
        pool = c.execute(
            "SELECT monthly_credit_pool FROM client_settings WHERE tenant_id = %s", (tenant,)
        ).fetchone()[0]
        moved = c.execute(
            "SELECT coalesce(sum(credits) FILTER (WHERE kind IN ('reserve', 'settle')), 0)"
            " - coalesce(sum(credits) FILTER (WHERE kind = 'release'), 0)"
            " FROM credit_entries WHERE tenant_id = %s",
            (tenant,),
        ).fetchone()[0]
    return float(pool - moved)


def _set_balance(tenant: UUID, amount: float) -> None:
    """Give the client a monthly pool of ``amount`` credits."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO client_settings (tenant_id, account_cap_per_user, idle_expiry_days,"
            " monthly_credit_pool, fastest_schedule, updated_by) VALUES (%s, 34, 7, %s, '3d',"
            " 'test') ON CONFLICT (tenant_id) DO UPDATE SET monthly_credit_pool ="
            " EXCLUDED.monthly_credit_pool",
            (tenant, int(amount)),
        )


async def _create(tenant: UUID, domain: str, key: str):
    return await runs.create_run(
        runs.RunBody(domain=domain),
        REQUEST,
        key,
        Identity("u1", tenant, "admin"),  # type: ignore[arg-type]
    )


async def test_create_run_reserves_credits_in_the_same_transaction(tenant_a: UUID):
    _set_balance(tenant_a, 1000)
    out = await _create(tenant_a, "acme.com", "idem-0001-aaaa")
    assert out["status"] == "QUEUED"
    assert _balance(tenant_a) == 1000 - _estimate()
    with psycopg.connect(ADMIN_DSN) as c:
        row = c.execute(
            "SELECT e.kind, e.credits, j.kind, j.requested_by FROM credit_entries e"
            " JOIN research_jobs j ON j.id = e.job_id WHERE j.run_id = %s",
            (out["run_id"],),
        ).fetchone()
    assert row == ("reserve", _estimate(), "new_research", "u1")


async def test_replay_with_the_same_key_returns_the_same_run_and_does_not_charge_twice(
    tenant_a: UUID,
):
    _set_balance(tenant_a, 1000)
    a = await _create(tenant_a, "acme.com", "idem-0002-aaaa")
    b = await _create(tenant_a, "acme.com", "idem-0002-aaaa")
    assert a["run_id"] == b["run_id"]
    assert _balance(tenant_a) == 1000 - _estimate()


async def test_same_key_with_a_different_domain_is_rejected(tenant_a: UUID):
    """Known defect 5, fixed by the request hash (ADR-0016); this was a strict xfail."""
    await _create(tenant_a, "acme.com", "idem-0003-aaaa")
    with pytest.raises(HTTPException) as e:
        await _create(tenant_a, "other.com", "idem-0003-aaaa")
    assert e.value.status_code == 409


async def test_created_run_has_its_tracker_and_slice_zero_is_queued(tenant_a: UUID, container):
    out = await _create(tenant_a, "seeded.com", "idem-0005-aaaa")
    with psycopg.connect(ADMIN_DSN) as c:
        rows = c.execute(
            "SELECT count(*) FROM coverage_rows WHERE run_id = %s", (out["run_id"],)
        ).fetchone()[0]
    assert rows == 33
    assert container.scheduler.queued[-1][1:] == (UUID(out["run_id"]), 0)


async def test_insufficient_credits_returns_402_and_leaves_nothing_behind(tenant_a: UUID):
    _set_balance(tenant_a, _estimate() - 1)
    with pytest.raises(HTTPException) as e:
        await _create(tenant_a, "poor.com", "idem-0004-aaaa")
    assert e.value.status_code == 402
    assert _balance(tenant_a) == _estimate() - 1
    with psycopg.connect(ADMIN_DSN) as c:
        n = c.execute(
            "SELECT count(*) FROM account_runs WHERE tenant_id = %s", (tenant_a,)
        ).fetchone()[0]
        d = c.execute("SELECT count(*) FROM accounts WHERE tenant_id = %s", (tenant_a,)).fetchone()[
            0
        ]
    assert n == 0 and d == 0  # the whole transaction rolled back


async def test_ten_parallel_runs_cannot_overspend_the_pool(tenant_a: UUID):
    """Ten concurrent runs against five runs' worth of credits allow exactly five reservations."""
    _set_balance(tenant_a, 5 * _estimate())
    res = await asyncio.gather(
        *[_create(tenant_a, f"d{i}.com", f"idem-par-{i:04d}") for i in range(10)],
        return_exceptions=True,
    )
    ok = [r for r in res if isinstance(r, dict)]
    denied = [r for r in res if isinstance(r, HTTPException) and r.status_code == 402]
    assert len(ok) == 5 and len(denied) == 5
    assert _balance(tenant_a) == 0.0
