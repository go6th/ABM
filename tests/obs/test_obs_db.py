"""ObsGuard on real Postgres (synthetic tenants): the cost rollup equals the ledger exactly and is
idempotent (O6); the stuck scan records only stuck runs (O12); the isolation prober finds a
deliberately broken policy (O11); timelines come from durable events and name missing sources
(O7); every runbook query runs (O14); the ops routes are operator-only, fail closed and carry no
content (O15)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from obs import canary, costs, stuck, timings
from tests.spend.conftest import admin

# asyncio is marked per coroutine test: a module-wide mark also tags the sync tests (warning)
pytestmark = [pytest.mark.requires_postgres]
ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 3, 2, 12, tzinfo=UTC)
TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "CANCELED", "BUDGET_EXCEEDED"}


def new_run(tenant: UUID, status: str = "RUNNING", created: datetime = NOW) -> UUID:
    domain = f"obs-{uuid4().hex[:8]}.example.com"
    acct = admin(
        "INSERT INTO accounts (tenant_id, owner_user_id, domain)"
        " VALUES (%s, 'test-owner', %s) RETURNING id",
        tenant,
        domain,
    )[0][0]
    finished = created + timedelta(minutes=1) if status in TERMINAL else None
    return admin(  # type: ignore[return-value]
        "INSERT INTO account_runs (tenant_id, account_id, graph_version, budget_json,"
        " reserved_credits, idempotency_key, status, created_at, finished_at)"
        " VALUES (%s, %s, 'test', '{}', 1, %s, %s, %s, %s) RETURNING id",
        tenant,
        acct,
        "k-" + uuid4().hex[:10],
        status,
        created,
        finished,
    )[0][0]


def unit(tenant: UUID, run: UUID, key: str, deps: list[str] | None = None) -> None:
    admin(
        "INSERT INTO work_units (tenant_id, run_id, unit_key, unit_type, body, depends_on,"
        " plan_hash) VALUES (%s, %s, %s, 'stage', 'b', %s, %s)",
        tenant,
        run,
        key,
        deps or [],
        "0" * 64,
    )


def ledger(
    tenant: UUID, run: UUID, provider: str, usd: str, source: str, route: str | None = "need"
) -> None:
    admin(
        "INSERT INTO tool_calls (tenant_id, run_id, tool, args_hash, cost_usd, provider_code,"
        " route, cost_source) VALUES (%s, %s, 'web_search', %s, %s, %s, %s, %s)",
        tenant,
        run,
        uuid4().hex,
        Decimal(usd),
        provider,
        route,
        source,
    )


@pytest.mark.asyncio
async def test_cost_rollups_equal_the_ledger_exactly(tenant_a: UUID, tenant_b: UUID) -> None:
    run = new_run(tenant_a)
    ledger(tenant_a, run, "p_search", "0.010000", "documented")
    ledger(tenant_a, run, "p_search", "0.005001", "documented")
    ledger(tenant_a, run, "p_people", "0.200000", "provider_reported", None)
    ledger(tenant_a, run, "p_people", "0", "unknown", None)
    ledger(tenant_a, run, "p_jobs", "0", "prepulled")
    other = new_run(tenant_b)
    ledger(tenant_b, other, "p_search", "9.000000", "documented")  # never in tenant A's rollup
    first = await costs.refresh(tenant_txn, tenant_a, run)
    again = await costs.refresh(tenant_txn, tenant_a, run)  # idempotent upsert
    assert first == again
    rows = admin(
        "SELECT provider_code, sum(calls), sum(cost_micros), sum(unknown_cost_calls),"
        " min(plan_tier) FROM ops_cost_rollups WHERE run_id = %s"
        " GROUP BY provider_code ORDER BY provider_code",
        run,
    )
    assert [(r[0], int(r[1]), int(r[2]), int(r[3])) for r in rows] == [
        ("p_jobs", 1, 0, 0),
        ("p_people", 2, 200000, 1),
        ("p_search", 2, 15001, 0),
    ]
    assert all(r[4] for r in rows)  # the tenant's plan, never content
    lines = admin("SELECT count(*) FROM ops_cost_rollups WHERE run_id = %s", run)[0][0]
    assert lines == 4  # one line per (provider, route, cost source); a refresh adds none
    rec = await costs.reconcile(tenant_txn, tenant_a, run)
    assert rec == {
        "rollup_micros": 215001,
        "ledger_micros": 215001,
        "drift_micros": 0,
        "unknown_cost_calls": 1,
    }
    admin(
        "UPDATE ops_cost_rollups SET cost_micros = cost_micros + 1 WHERE run_id = %s AND"
        " provider_code = 'p_search'",
        run,
    )
    assert (await costs.reconcile(tenant_txn, tenant_a, run))["drift_micros"] == 1
    async with tenant_txn(tenant_b) as s:  # RLS: tenant B sees none of A's rollup
        from sqlalchemy import text

        seen = (
            await s.execute(
                text("SELECT count(*) FROM ops_cost_rollups WHERE run_id = :r"), {"r": run}
            )
        ).scalar_one()
    assert seen == 0


@pytest.mark.asyncio
async def test_stuck_scan_records_only_stuck_runs(tenant_a: UUID) -> None:
    stale_queued = new_run(tenant_a, "QUEUED", NOW - timedelta(minutes=5))
    fresh_queued = new_run(tenant_a, "QUEUED", NOW - timedelta(seconds=20))
    quiet = new_run(tenant_a, "RUNNING", NOW - timedelta(hours=2))
    busy = new_run(tenant_a, "RUNNING", NOW - timedelta(hours=2))
    unit(tenant_a, busy, "u1")
    admin(
        "INSERT INTO work_unit_events (tenant_id, run_id, unit_key, event, at)"
        " VALUES (%s, %s, 'u1', 'started', %s)",
        tenant_a,
        busy,
        NOW - timedelta(minutes=1),
    )
    paused = new_run(tenant_a, "AWAITING_APPROVAL", NOW - timedelta(hours=5))
    admin(
        "UPDATE account_runs SET approval_deadline = %s WHERE id = %s",
        NOW + timedelta(hours=1),
        paused,
    )
    done = new_run(tenant_a, "COMPLETED", NOW - timedelta(days=2))
    found = await stuck.scan(tenant_txn, tenant_a, NOW, stuck.quiet_seconds(240, 4), page=2)
    flagged = {r: why for r, why, _ in found}
    assert flagged == {str(stale_queued): "queued_too_long", str(quiet): "no_activity"}
    for r in (fresh_queued, busy, paused, done):
        assert str(r) not in flagged
    rows = admin("SELECT run_id, reason FROM stuck_run_observations WHERE tenant_id = %s", tenant_a)
    assert {(str(r), w) for r, w in rows} == set(flagged.items())
    # the detector only observes: no run changed state
    assert admin("SELECT status FROM account_runs WHERE id = %s", stale_queued)[0][0] == "QUEUED"


@pytest.mark.asyncio
async def test_the_prober_detects_a_broken_policy(tenant_a: UUID, tenant_b: UUID) -> None:
    run_b = new_run(tenant_b)
    acct_b = admin("SELECT account_id FROM account_runs WHERE id = %s", run_b)[0][0]
    markers = {"accounts": acct_b, "account_runs": run_b}
    assert await canary.probe(tenant_txn, tenant_a, tenant_b, markers, NOW) == []
    admin("CREATE POLICY obs_test_leak ON accounts TO app_rw USING (true)")  # a broken policy
    try:
        assert await canary.probe(tenant_txn, tenant_a, tenant_b, markers, NOW) == ["accounts"]
    finally:
        admin("DROP POLICY obs_test_leak ON accounts")
    leaks = admin(
        "SELECT leaks FROM canary_probe_results WHERE tenant_id = %s ORDER BY id", tenant_a
    )
    assert [r[0] for r in leaks] == [0, 1]
    with pytest.raises(ValueError):
        await canary.probe(tenant_txn, tenant_a, tenant_b, {"pg_authid": 1}, NOW)


@pytest.mark.asyncio
async def test_timings_come_from_events_and_report_missing_sources(
    tenant_a: UUID, tenant_b: UUID
) -> None:
    run = new_run(tenant_a, created=NOW)
    unit(tenant_a, run, "plan")
    unit(tenant_a, run, "research", ["plan"])
    for key, event, sec in (
        ("plan", "queued", 0),
        ("plan", "started", 5),
        ("plan", "finished", 65),
        ("research", "queued", 65),
        ("research", "started", 70),
        ("research", "finished", 190),
    ):
        admin(
            "INSERT INTO work_unit_events (tenant_id, run_id, unit_key, event, at)"
            " VALUES (%s, %s, %s, %s, %s)",
            tenant_a,
            run,
            key,
            event,
            NOW + timedelta(seconds=sec),
        )
    tl = await timings.timeline(tenant_txn, tenant_a, run)
    assert {t.key for t in tl.units.timings} == {"plan", "research"}
    assert tl.units.makespan_s == pytest.approx(190)
    assert "stage_attempts" in tl.missing  # a later work package's source: reported, not guessed
    with pytest.raises(LookupError):
        await timings.timeline(tenant_txn, tenant_b, run)  # another tenant's run
    assert timings.percentile([3.0, 1.0, 2.0], 0.5) == 2.0 and timings.percentile([], 0.9) is None


def test_every_runbook_query_runs(tenant_a: UUID) -> None:
    new_run(tenant_a, "FAILED")
    from tests.conftest import ADMIN_DSN

    queries = sorted((ROOT / "docs/runbooks/queries").glob("*.sql"))
    assert len(queries) >= 10
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("SET default_transaction_read_only = on")  # first checks never write
        for q in queries:
            c.execute(q.read_text()).fetchall()


@pytest.mark.asyncio
async def test_ops_routes_are_operator_only_and_carry_no_content(tenant_a: UUID) -> None:
    from tests.api.test_spend_v2_routes import app_for, call

    run = new_run(tenant_a, "FAILED")
    admin("UPDATE account_runs SET finished_reason = 'stage_error' WHERE id = %s", run)
    ledger(tenant_a, run, "p_search", "0.010000", "documented")
    domain = admin(
        "SELECT a.domain FROM accounts a JOIN account_runs r ON r.account_id = a.id"
        " WHERE r.id = %s",
        run,
    )[0][0]
    paths = [
        f"/operator/v1/runs/{run}/timeline",
        f"/operator/v1/ops/cost/{run}",
        "/operator/v1/ops/failures",
        "/operator/v1/ops/stuck",
    ]
    tenant_app, _ = app_for(tenant_a)  # a tenant admin is never an operator
    for p in paths:
        assert (await call(tenant_app, "GET", p)).status_code == 503
    op_app, _ = app_for(tenant_a, operator=True)
    for p in paths:
        res = await call(op_app, "GET", p)
        assert res.status_code == 200, (p, res.text)
        assert domain not in res.text
    tl = (await call(op_app, "GET", paths[0])).json()
    assert tl["trace_id"] == f"{run.int:032x}"
    cost = (await call(op_app, "GET", paths[1])).json()
    assert cost["reconcile"]["drift_micros"] == 0 and cost["lines"][0]["cost_micros"] == 10000
    fails = (await call(op_app, "GET", paths[2])).json()["rows"]
    assert {"status": "FAILED", "reason": "stage_error", "runs": 1} in fails
    assert (await call(op_app, "GET", f"/operator/v1/runs/{uuid4()}/timeline")).status_code == 404
