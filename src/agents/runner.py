"""Time-sliced, resumable execution using the checkpoint as its source of truth."""

from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import UUID, uuid4

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from sqlalchemy import text

from src.agents.graph import BudgetExceeded, RunState, build_graph
from src.db.session import tenant_txn

SLICE_SECONDS = int(os.getenv("SLICE_SECONDS", "240"))


async def _dedicated_tenant_conn(
    tenant_id: UUID,
) -> psycopg.AsyncConnection[dict[str, Any]]:
    conn = await psycopg.AsyncConnection.connect(
        os.environ["CHECKPOINT_DATABASE_URL"],  # app_rw, direct to writer (no txn-mode proxy)
        autocommit=True,
        row_factory=dict_row,
        prepare_threshold=0,
    )
    # Session-scoped (is_local=false) because autocommit has no long transaction. Safe only because
    # this connection is private to one run and is closed in `finally`; it never returns to a pool.
    await conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
    return conn


async def _persist_terminal_status(
    tenant_id: UUID, run_id: UUID, lease_token: UUID, status: str
) -> str:
    if status == "COMPLETED":
        async with tenant_txn(tenant_id) as session:
            ready = (
                await session.execute(
                    text(
                        "SELECT r.expected_coverage_rows = ("
                        "SELECT count(*) FROM coverage_rows c "
                        "WHERE c.tenant_id = r.tenant_id AND c.run_id = r.id "
                        "AND c.checklist_version = r.checklist_version"
                        ") AND NOT EXISTS ("
                        "SELECT 1 FROM coverage_rows c "
                        "WHERE c.tenant_id = r.tenant_id AND c.run_id = r.id "
                        "AND c.checklist_version = r.checklist_version "
                        "AND c.priority = 'High' AND (c.status IS NULL OR "
                        "(c.status = 'Blocked' AND nullif(trim(c.blocked_reason), '') IS NULL) "
                        "OR (c.status <> 'Blocked' AND (jsonb_array_length(c.queries) = 0 "
                        "OR jsonb_array_length(c.tool_categories) = 0 OR c.result_count IS NULL))"
                        ")) AS ready FROM account_runs r "
                        "WHERE r.tenant_id = (SELECT app_tenant()) AND r.id = :run_id"
                    ),
                    {"run_id": run_id},
                )
            ).scalar_one_or_none()
        if ready is None:
            raise LookupError(f"run {run_id} disappeared before terminal status persistence")
        if not ready:
            status = "PARTIAL"

    async with tenant_txn(tenant_id) as session:
        updated = await session.execute(
            text(
                "UPDATE account_runs SET status = :status "
                "WHERE tenant_id = (SELECT app_tenant()) AND id = :run_id "
                "AND worker_lease_token = :lease_token RETURNING id"
            ),
            {"status": status, "run_id": run_id, "lease_token": lease_token},
        )
        if updated.scalar_one_or_none() is None:
            raise RuntimeError(f"run {run_id} terminal status could not be persisted")
    return status


async def execute_slice(tenant_id: UUID, run_id: UUID) -> dict[str, Any]:
    lease_token = uuid4()
    lease_seconds = max(SLICE_SECONDS + 60, 300)
    async with tenant_txn(tenant_id) as session:
        run = (
            await session.execute(
                text(
                    "SELECT a.domain, r.budget_json ->> 'usd' AS budget_usd, r.status "
                    "FROM account_runs AS r JOIN accounts AS a "
                    "ON a.id = r.account_id AND a.tenant_id = r.tenant_id "
                    "WHERE r.id = :run_id"
                ),
                {"run_id": run_id},
            )
        ).first()
        if run is None:
            raise LookupError(f"run {run_id} does not exist for tenant {tenant_id}")
        if run.status in {"COMPLETED", "PARTIAL", "FAILED", "CANCELED", "BUDGET_EXCEEDED"}:
            return {"done": True, "status": run.status}
        claimed = await session.execute(
            text(
                "UPDATE account_runs SET worker_lease_token = :token, "
                "worker_lease_expires_at = now() + make_interval(secs => :lease_seconds) "
                "WHERE tenant_id = (SELECT app_tenant()) AND id = :run_id "
                "AND (worker_lease_expires_at IS NULL OR worker_lease_expires_at <= now()) "
                "RETURNING id"
            ),
            {"token": lease_token, "lease_seconds": lease_seconds, "run_id": run_id},
        )
        lease_acquired = claimed.first() is not None
    if not lease_acquired:
        await asyncio.sleep(0.5)
        return {"done": False, "lease_busy": True}
    domain = str(run.domain)
    budget_usd = float(run.budget_usd)
    config: RunnableConfig = {
        "configurable": {"thread_id": f"{tenant_id}:{run_id}"},
        "recursion_limit": 60,
    }
    conn: psycopg.AsyncConnection[dict[str, Any]] | None = None
    try:
        conn = await _dedicated_tenant_conn(tenant_id)
        # saver.setup() is run once by the migrator job, never here.
        graph = build_graph().compile(checkpointer=AsyncPostgresSaver(conn))
        snap = await graph.aget_state(config)
        if snap.values and not snap.next:
            status = await _persist_terminal_status(
                tenant_id,
                run_id,
                lease_token,
                snap.values.get("status", "COMPLETED"),
            )
            return {"done": True, "status": status}
        initial: RunState | None = (
            None
            if snap.values
            else {
                "tenant_id": str(tenant_id),
                "run_id": str(run_id),
                "domain": domain,
                "budget_usd": budget_usd,
            }
        )
        try:
            async with asyncio.timeout(SLICE_SECONDS):
                await graph.ainvoke(initial, config)  # None => resume from last checkpoint
        except TimeoutError:
            return {"done": False}  # next slice resumes; ledger prevents re-billing
        except BudgetExceeded:
            status = await _persist_terminal_status(
                tenant_id, run_id, lease_token, "BUDGET_EXCEEDED"
            )
            return {"done": True, "status": status}
        final = await graph.aget_state(config)
        if final.next:  # paused on an interrupt (HITL gate)
            return {"done": False, "awaiting": True}
        status = await _persist_terminal_status(
            tenant_id,
            run_id,
            lease_token,
            final.values.get("status", "COMPLETED"),
        )
        return {"done": True, "status": status}
    finally:
        if conn is not None:
            await conn.close()
        async with tenant_txn(tenant_id) as session:
            await session.execute(
                text(
                    "UPDATE account_runs SET worker_lease_token = NULL, "
                    "worker_lease_expires_at = NULL WHERE tenant_id = (SELECT app_tenant()) "
                    "AND id = :run_id AND worker_lease_token = :token"
                ),
                {"run_id": run_id, "token": lease_token},
            )
