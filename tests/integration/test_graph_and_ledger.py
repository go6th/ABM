"""Integration checks for the starter graph, runner, and ledger under forced RLS.

The broker is stubbed. Defect details are in skill reference 13.
"""

import asyncio
import json
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text

import src.agents.graph as graph_mod
from src.agents.graph import ledgered_call
from src.agents.runner import execute_slice
from src.db.session import tenant_txn
from tests.conftest import ADMIN_DSN

pytestmark = pytest.mark.asyncio


def _make_run(tenant_id: UUID, budget_usd: float = 10.0) -> UUID:
    run_id = uuid4()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as connection:
        account_id = connection.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
            (tenant_id, f"{run_id.hex}.example.com"),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO account_runs "
            "(id, tenant_id, account_id, graph_version, budget_json, "
            "reserved_credits, idempotency_key) "
            "VALUES (%s, %s, %s, 'test', %s, 1, %s)",
            (
                run_id,
                tenant_id,
                account_id,
                json.dumps({"usd": budget_usd}),
                f"graph-{run_id.hex}",
            ),
        )
    return run_id


async def test_graph_runs_to_completion_under_rls_and_sums_parallel_spend(
    tenant_a: UUID, monkeypatch
):
    """Parallel branches add 0.5 each and write checkpoints under forced RLS."""

    async def fake_ledgered_call(tenant_id, run_id, tool, args, fn):
        return {"facts": [{"branch": args["branch"]}], "cost_usd": 0.5}

    monkeypatch.setattr(graph_mod, "ledgered_call", fake_ledgered_call)
    run_id = _make_run(tenant_a)
    out = await execute_slice(tenant_a, run_id)
    assert out == {"done": True, "status": "COMPLETED"}
    with psycopg.connect(ADMIN_DSN) as c:
        threads = {r[0] for r in c.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()}
    assert f"{tenant_a}:{run_id}" in threads


async def test_resume_of_finished_run_is_a_noop(tenant_a: UUID, monkeypatch):
    async def fake(tenant_id, run_id, tool, args, fn):
        return {"facts": [], "cost_usd": 0.1}

    monkeypatch.setattr(graph_mod, "ledgered_call", fake)
    run_id = _make_run(tenant_a)
    await execute_slice(tenant_a, run_id)
    again = await execute_slice(tenant_a, run_id)
    assert again["done"] is True


async def test_budget_exceeded_is_reported(tenant_a: UUID, monkeypatch):
    async def fake(tenant_id, run_id, tool, args, fn):
        return {"facts": [], "cost_usd": 0.1}

    monkeypatch.setattr(graph_mod, "ledgered_call", fake)
    out = await execute_slice(tenant_a, _make_run(tenant_a, budget_usd=0))
    assert out == {"done": True, "status": "BUDGET_EXCEEDED"}


async def test_ledger_replays_a_completed_call_without_paying_again(tenant_a: UUID):
    calls = []

    async def paid(**kw):
        calls.append(kw)
        return {"facts": [1], "cost_usd": 0.5}

    run_id = str(uuid4())
    a = await ledgered_call(str(tenant_a), run_id, "broker:news", {"domain": "x.com"}, paid)
    b = await ledgered_call(str(tenant_a), run_id, "broker:news", {"domain": "x.com"}, paid)
    assert a == b and len(calls) == 1


@pytest.mark.xfail(
    strict=True,
    reason="defect 3: no per-run lease and no intent row, and the paid call sits between two "
    "transactions, so two concurrent identical calls both pay. Remove this xfail when "
    "a lease plus an intent row (or a provider idempotency key) closes the window.",
)
async def test_concurrent_identical_calls_pay_once(tenant_a: UUID):
    calls = []

    async def paid(**kw):
        calls.append(kw)
        await asyncio.sleep(0.05)  # widen the window between response and ledger insert
        return {"facts": [], "cost_usd": 0.5}

    run_id = str(uuid4())
    await asyncio.gather(
        *[
            ledgered_call(str(tenant_a), run_id, "broker:jobs", {"domain": "y.com"}, paid)
            for _ in range(2)
        ]
    )
    assert len(calls) == 1


async def test_direct_access_to_a_chunk_partition_is_denied(tenant_a: UUID):
    """Direct access to a child partition must be denied to app_rw."""
    with pytest.raises(Exception, match="permission denied"):
        async with tenant_txn(tenant_a) as s:
            await s.execute(text("SELECT count(*) FROM chunks_p0"))
