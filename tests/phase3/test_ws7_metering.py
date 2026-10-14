"""WS7 metering (MT1 to MT3, D-67). SYNTHETIC tenants only.

Lying scenarios: a retried action that would count twice, a settlement replayed after a crash,
100 requests racing for the last unit of a limit, a soft threshold that would notify on every
request, drift planted between the usage stream and the ledger, a usage view that would show cost.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.db.session import tenant_txn
from metering import events, limits, reconcile
from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import Browser, Plane, add_member, add_oidc_connection, new_tenant, uid

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def _db(sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(ADMIN_DSN) as c:
        return c.execute(sql, params).fetchall()


async def test_plan_seed_matches_the_configuration() -> None:
    cfg = yaml.safe_load((CONFIG_DIR / "plans.yaml").read_text())["plans"]
    rows = _db("SELECT plan_code, dimension, limit_value FROM plan_limits WHERE version = 1")
    assert {(p, d): v for p, d, v in rows} == {
        (p, d): v for p, dims in cfg.items() for d, v in dims.items()
    }


async def test_usage_events_are_idempotent_and_settlement_emits_once() -> None:
    from account_intelligence.runs import lifecycle

    t = new_tenant("mt-idem")
    async with tenant_txn(t) as s:
        assert await events.record(s, "export_created", "pkg-1", NOW)
        assert not await events.record(s, "export_created", "pkg-1", NOW)
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        acc = c.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, 'mt.example') RETURNING id", (t,)
        ).fetchone()[0]
        run = c.execute(
            "INSERT INTO account_runs (tenant_id, account_id, status, graph_version, budget_json,"
            " reserved_credits, idempotency_key, budget_usd, finished_at) VALUES (%s, %s,"
            " 'COMPLETED', 'g', '{}', 1, %s, 5, now()) RETURNING id",
            (t, acc, uuid4().hex),
        ).fetchone()[0]
        job = c.execute(  # the run's research job holds its credits (the single ledger)
            "INSERT INTO research_jobs (tenant_id, account_id, kind, scope, status, period,"
            " credits_estimated, requested_by, run_id, started_at) VALUES (%s, %s,"
            " 'new_research', 'full', 'running', to_char(now(), 'YYYY-MM'), 1, 'u', %s, now())"
            " RETURNING id",
            (t, acc, run),
        ).fetchone()[0]
        c.execute(
            "INSERT INTO credit_entries (tenant_id, period, kind, credits, job_id, purpose, actor)"
            " VALUES (%s, to_char(now(), 'YYYY-MM'), 'reserve', 1, %s, 'new_research', 'u')",
            (t, job),
        )
    for _ in range(3):  # a settlement replayed after a crash, twice
        async with tenant_txn(t) as s:
            await lifecycle.settle_once(s, run)
    assert (
        _db("SELECT count(*) FROM usage_events WHERE tenant_id = %s AND kind = 'run_settled'", t)[
            0
        ][0]
        == 1
    )


async def _consume(t: UUID, dim: str) -> str:
    try:
        async with tenant_txn(t) as s:
            await limits.consume(s, dim, NOW)
        return "ok"
    except limits.LimitReached:
        return "refused"


async def test_limits_are_atomic_under_concurrency() -> None:
    t = new_tenant("mt-race")
    results = await asyncio.gather(*(_consume(t, "runs_per_period") for _ in range(100)))
    assert results.count("ok") == 20 and results.count("refused") == 80  # pilot proposal: 20
    assert (
        _db(
            "SELECT used FROM usage_counters WHERE tenant_id = %s AND dimension ="
            " 'runs_per_period'",
            t,
        )[0][0]
        == 20
    )
    assert (
        _db(
            "SELECT count(*) FROM notification_outbox WHERE tenant_id = %s AND template ="
            " 'plan_usage_near_limit'",
            t,
        )[0][0]
        == 1
    )  # the soft threshold, once
    async with tenant_txn(t) as s:
        with pytest.raises(ValueError):
            await limits.consume(s, "credits", NOW)
        with pytest.raises(ValueError):
            await limits.release(s, "runs_per_period", NOW)
    # a quota override (operator plane) is a newer version that changes the limit
    other = new_tenant("mt-plan")
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO plan_assignments (tenant_id, version, plan_code, assigned_by,"
            " reason_code, created_at) VALUES (%s, 1, 'standard', 'op', 'upgrade', now())",
            (other,),
        )
    async with tenant_txn(other) as s:
        assert (await limits.limits(s))["runs_per_period"] == 100


async def _admin(plane: Plane, browser: Browser, name: str) -> UUID:
    t = new_tenant(name)
    slug = f"acme-{uid()}"
    add_oidc_connection(t, plane, slug)
    add_member(t, f"{slug}:root", "admin")
    await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    return t


async def test_the_api_refuses_beyond_the_plan_in_units(plane: Plane, browser: Browser) -> None:
    t = await _admin(plane, browser, "mt-api")
    for i in range(10):
        assert (
            await browser.call("POST", "/v1/accounts", json={"domain": f"a{i}.example"})
        ).status_code == 201
    over = await browser.call("POST", "/v1/accounts", json={"domain": "a10.example"})
    assert over.status_code == 402 and "plan_limit_reached" in over.text
    assert "10 accounts" in over.json()["detail"]
    for word in ("credit", "$", "usd", "cost"):
        assert word not in over.text.lower()
    again = await browser.call("POST", "/v1/accounts", json={"domain": "a0.example"})
    assert again.status_code == 201 and again.json()["created"] is False  # not counted twice
    assert (
        _db("SELECT count(*) FROM usage_events WHERE tenant_id = %s AND kind = 'account_added'", t)[
            0
        ][0]
        == 10
    )
    usage = (await browser.client.get("/v1/usage")).json()
    accounts = next(i for i in usage["items"] if i["dimension"] == "accounts")
    assert (accounts["used"], accounts["limit"], accounts["percent"]) == (10, 10, 100)
    csv_text = (await browser.client.get("/v1/usage/export")).text
    assert "account_added,10,10" in csv_text
    for word in ("credit", "cost", "usd", "p_"):
        assert word not in (str(usage) + csv_text).lower(), word


async def test_reconcile_catches_planted_drift(plane: Plane, browser: Browser) -> None:
    t = await _admin(plane, browser, "mt-drift")
    started = await browser.call(
        "POST",
        "/v1/runs",
        json={"domain": "drift.example"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert started.status_code == 202, started.text
    run = started.json()["run_id"]
    replay_key = uuid4().hex
    for _ in range(2):  # a retried request with the same key counts once
        await browser.call(
            "POST",
            "/v1/runs",
            json={"domain": "drift.example"},
            headers={"Idempotency-Key": replay_key},
        )
    async with tenant_txn(t) as s:
        clean = await reconcile.reconcile(s, plane.clock.now())
    assert clean.ok, clean
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("DELETE FROM usage_events WHERE tenant_id = %s AND subject_id = %s", (t, run))
        c.execute(
            "INSERT INTO usage_events (tenant_id, kind, subject_id, idempotency_key,"
            " period, at) VALUES (%s, 'run_settled', 'ghost-run', 'run_settled:ghost-run',"
            " '2026-03', now())",
            (t,),
        )
        c.execute(
            "UPDATE usage_counters SET used = used + 5 WHERE tenant_id = %s"
            " AND dimension = 'accounts'",
            (t,),
        )
    async with tenant_txn(t) as s:
        drift = await reconcile.reconcile(s, plane.clock.now())
    assert run in drift.missing_run_started
    assert drift.unexpected_run_settled == ["ghost-run"]
    assert "accounts" in drift.counter_mismatch and not drift.ok
