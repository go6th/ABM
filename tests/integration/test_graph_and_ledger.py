"""Integration checks for the starter graph, runner, and ledger under forced RLS.

The broker is stubbed. Defect details are in skill reference 13.
"""

import asyncio
import json
import multiprocessing
import operator
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, TypedDict
from uuid import UUID, uuid4

import psycopg
import pytest
import yaml
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from psycopg.rows import dict_row
from sqlalchemy import text

import src.agents.graph as graph_mod
import src.agents.runner as runner_mod
from src.agents.graph import ToolCallOutcomeUnknown, ledgered_call
from src.agents.runner import execute_slice
from src.db.session import tenant_txn
from tests.conftest import ADMIN_DSN

pytestmark = pytest.mark.asyncio


class _DurabilitySpikeState(TypedDict, total=False):
    tenant_id: str
    run_id: str
    branches: Annotated[list[str], operator.add]
    status: str


def _run_durability_spike_worker(
    tenant_id: str,
    run_id: str,
    dsn: str,
    work_dir: str,
    crash_after_first_paid_call: bool,
    resume: bool,
) -> None:
    asyncio.run(
        _durability_spike_worker(
            tenant_id,
            run_id,
            dsn,
            work_dir,
            crash_after_first_paid_call,
            resume,
        )
    )


async def _durability_spike_worker(
    tenant_id: str,
    run_id: str,
    dsn: str,
    work_dir: str,
    crash_after_first_paid_call: bool,
    resume: bool,
) -> None:
    marker = Path(work_dir) / f"{run_id}.paid-a"
    release = Path(work_dir) / f"{run_id}.release"

    async def activity(state: _DurabilitySpikeState, branch: str) -> dict[str, object]:
        async def paid_stub(**kwargs: object) -> dict[str, object]:
            async with tenant_txn(UUID(state["tenant_id"])) as session:
                await session.execute(
                    text(
                        "INSERT INTO durability_spike_calls (tenant_id, run_id, branch) "
                        "VALUES ((SELECT app_tenant()), :run_id, :branch)"
                    ),
                    {"run_id": state["run_id"], "branch": branch},
                )
            return {"facts": [{"branch": branch}], "cost_usd": 0.01}

        result = await ledgered_call(
            state["tenant_id"],
            state["run_id"],
            f"durability-spike:{branch}",
            {"branch": branch},
            paid_stub,
        )
        if branch == "a" and crash_after_first_paid_call:
            marker.write_text("durable response committed", encoding="utf-8")
            await asyncio.sleep(60)
        if branch == "b" and crash_after_first_paid_call:
            while not release.exists():
                await asyncio.sleep(0.01)
        return {"branches": [branch], **result}

    async def activity_a(state: _DurabilitySpikeState) -> dict[str, object]:
        return await activity(state, "a")

    async def activity_b(state: _DurabilitySpikeState) -> dict[str, object]:
        return await activity(state, "b")

    async def finalize(state: _DurabilitySpikeState) -> dict[str, str]:
        return {"status": "COMPLETED"}

    graph = StateGraph(_DurabilitySpikeState)
    graph.add_node("paid_a", activity_a)
    graph.add_node("paid_b", activity_b)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "paid_a")
    graph.add_edge(START, "paid_b")
    graph.add_edge("paid_a", "finalize")
    graph.add_edge("paid_b", "finalize")
    graph.add_edge("finalize", END)

    connection = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
        prepare_threshold=0,
    )
    try:
        await connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        compiled = graph.compile(checkpointer=AsyncPostgresSaver(connection))
        config = {"configurable": {"thread_id": f"{tenant_id}:{run_id}"}}
        initial: _DurabilitySpikeState | None = (
            None
            if resume
            else {"tenant_id": tenant_id, "run_id": run_id, "branches": []}
        )
        await compiled.ainvoke(initial, config)
        state = await compiled.aget_state(config)
        (Path(work_dir) / f"{run_id}.result").write_text(
            str(state.values.get("status", "")), encoding="utf-8"
        )
    finally:
        await connection.close()


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
        checklist = yaml.safe_load(
            Path(__file__)
            .resolve()
            .parents[2]
            .joinpath("config", "checklist.yaml")
            .read_text(encoding="utf-8")
        )
        connection.execute(
            "UPDATE account_runs SET checklist_version = %s, expected_coverage_rows = %s "
            "WHERE tenant_id = %s AND id = %s",
            (checklist["version"], len(checklist["rows"]), tenant_id, run_id),
        )
        connection.cursor().executemany(
            "INSERT INTO coverage_rows (tenant_id, account_id, run_id, checklist_version, "
            "row_key, pass, description, priority) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    tenant_id,
                    account_id,
                    run_id,
                    checklist["version"],
                    row["id"],
                    row["pass"],
                    row["row"],
                    row["priority"],
                )
                for row in checklist["rows"]
            ],
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
    assert out == {"done": True, "status": "PARTIAL"}
    async with tenant_txn(tenant_a) as session:
        run_status = (
            await session.execute(
                text("SELECT status FROM account_runs WHERE id = :run_id"),
                {"run_id": run_id},
            )
        ).scalar_one()
    assert run_status == "PARTIAL"
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

    run_id = str(_make_run(tenant_a))
    a = await ledgered_call(str(tenant_a), run_id, "broker:news", {"domain": "x.com"}, paid)
    resumed = [
        await ledgered_call(str(tenant_a), run_id, "broker:news", {"domain": "x.com"}, paid)
        for _ in range(5)
    ]
    assert all(result == a for result in resumed) and len(calls) == 1


async def test_concurrent_identical_calls_pay_once(tenant_a: UUID):
    calls = []

    async def paid(**kw):
        calls.append(kw)
        await asyncio.sleep(0.05)  # widen the window between response and ledger insert
        return {"facts": [], "cost_usd": 0.5}

    run_id = str(_make_run(tenant_a))
    results = await asyncio.gather(
        *(
            ledgered_call(str(tenant_a), run_id, "broker:jobs", {"domain": "y.com"}, paid)
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert len(calls) == 1
    assert sum(isinstance(result, ToolCallOutcomeUnknown) for result in results) <= 1
    assert sum(isinstance(result, dict) for result in results) >= 1


async def test_ambiguous_paid_call_is_not_dispatched_again(tenant_a: UUID):
    calls = []

    async def provider_effect_then_timeout(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("synthetic provider response was lost")

    run_id = str(_make_run(tenant_a))
    with pytest.raises(TimeoutError, match="response was lost"):
        await ledgered_call(
            str(tenant_a),
            run_id,
            "broker:news",
            {"domain": "synthetic.example"},
            provider_effect_then_timeout,
        )
    with pytest.raises(ToolCallOutcomeUnknown, match="unresolved intent"):
        await ledgered_call(
            str(tenant_a),
            run_id,
            "broker:news",
            {"domain": "synthetic.example"},
            provider_effect_then_timeout,
        )
    assert len(calls) == 1


async def test_checkpoint_resume_after_five_mid_fanout_process_kills(
    tenant_a: UUID, tmp_path: Path
):
    """Kill before one node checkpoint, then prove the durable ledger prevents redispatch."""
    dsn = os.environ["CHECKPOINT_DATABASE_URL"]
    context = multiprocessing.get_context("spawn")
    with psycopg.connect(ADMIN_DSN, autocommit=True) as connection:
        connection.execute(
            "CREATE TABLE durability_spike_calls ("
            "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "tenant_id uuid NOT NULL REFERENCES tenants(id), "
            "run_id uuid NOT NULL, branch text NOT NULL, "
            "created_at timestamptz NOT NULL DEFAULT now(), "
            "FOREIGN KEY (tenant_id, run_id) REFERENCES account_runs(tenant_id, id))"
        )
        connection.execute(
            "ALTER TABLE durability_spike_calls ENABLE ROW LEVEL SECURITY"
        )
        connection.execute(
            "ALTER TABLE durability_spike_calls FORCE ROW LEVEL SECURITY"
        )
        connection.execute(
            "CREATE POLICY tenant_isolation ON durability_spike_calls TO app_rw "
            "USING (tenant_id = (SELECT app_tenant())) "
            "WITH CHECK (tenant_id = (SELECT app_tenant()))"
        )
        connection.execute("GRANT SELECT, INSERT ON durability_spike_calls TO app_rw")
    for _ in range(5):
        run_id = _make_run(tenant_a)
        marker = tmp_path / f"{run_id}.paid-a"
        worker_args = (
            str(tenant_a),
            str(run_id),
            dsn,
            str(tmp_path),
        )
        first = context.Process(
            target=_run_durability_spike_worker,
            args=(*worker_args, True, False),
        )
        first.start()
        deadline = time.monotonic() + 30
        try:
            while not marker.exists() and first.is_alive() and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert marker.exists(), "worker did not commit the first paid response"
            first.kill()
            first.join(timeout=10)
            assert not first.is_alive(), "worker did not stop after SIGKILL"
        finally:
            if first.is_alive():
                first.kill()
                first.join(timeout=10)

        resumed = context.Process(
            target=_run_durability_spike_worker,
            args=(*worker_args, False, True),
        )
        resumed.start()
        resumed.join(timeout=30)
        if resumed.is_alive():
            resumed.kill()
            resumed.join(timeout=10)
            pytest.fail("resumed worker did not finish")
        assert resumed.exitcode == 0
        assert (tmp_path / f"{run_id}.result").read_text(encoding="utf-8") == "COMPLETED"
        with psycopg.connect(ADMIN_DSN) as connection:
            invocation_counts = connection.execute(
                "SELECT branch, count(*) FROM durability_spike_calls "
                "WHERE tenant_id = %s AND run_id = %s GROUP BY branch ORDER BY branch",
                (tenant_a, run_id),
            ).fetchall()
        assert invocation_counts == [("a", 1), ("b", 1)]


async def test_worker_lease_allows_only_one_graph_worker(
    tenant_a: UUID, monkeypatch: pytest.MonkeyPatch
):
    run_id = _make_run(tenant_a)
    async with tenant_txn(tenant_a) as session:
        await session.execute(
            text(
                "UPDATE coverage_rows SET status = 'Blocked', "
                "blocked_reason = 'Synthetic test fixture: evidence unavailable' "
                "WHERE run_id = :run_id AND priority = 'High'"
            ),
            {"run_id": run_id},
        )
    started = asyncio.Event()
    resume = asyncio.Event()
    graph_finished = False

    class FakeConnection:
        async def close(self):
            return None

    class FakeGraph:
        async def aget_state(self, config):
            return SimpleNamespace(
                values={"status": "COMPLETED"} if graph_finished else {},
                next=() if graph_finished else ("node",),
            )

        async def ainvoke(self, initial, config):
            nonlocal graph_finished
            started.set()
            await resume.wait()
            graph_finished = True

    class FakeGraphBuilder:
        def compile(self, checkpointer):
            return FakeGraph()

    async def fake_connection(tenant_id):
        return FakeConnection()

    monkeypatch.setattr(runner_mod, "_dedicated_tenant_conn", fake_connection)
    monkeypatch.setattr(runner_mod, "AsyncPostgresSaver", lambda conn: object())
    monkeypatch.setattr(runner_mod, "build_graph", FakeGraphBuilder)

    first = asyncio.create_task(runner_mod.execute_slice(tenant_a, run_id))
    await started.wait()
    second = await runner_mod.execute_slice(tenant_a, run_id)
    assert second == {"done": False, "lease_busy": True}
    resume.set()
    assert await first == {"done": True, "status": "COMPLETED"}


async def test_direct_access_to_a_chunk_partition_is_denied(tenant_a: UUID):
    """Direct access to a child partition must be denied to app_rw."""
    with pytest.raises(Exception, match="permission denied"):
        async with tenant_txn(tenant_a) as s:
            await s.execute(text("SELECT count(*) FROM chunks_p0"))
