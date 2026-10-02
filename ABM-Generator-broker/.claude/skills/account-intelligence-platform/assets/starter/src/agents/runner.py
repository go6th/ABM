"""src/agents/runner.py - time-sliced, resumable execution. The checkpoint is the source of truth."""

from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import UUID

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row

from src.agents.graph import BudgetExceeded, build_graph

SLICE_SECONDS = int(os.getenv("SLICE_SECONDS", "240"))


async def _dedicated_tenant_conn(tenant_id: UUID) -> psycopg.AsyncConnection:
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


async def execute_slice(tenant_id: UUID, run_id: UUID, domain: str, budget_usd: float) -> dict[str, Any]:
    config = {"configurable": {"thread_id": f"{tenant_id}:{run_id}"}, "recursion_limit": 60}
    conn = await _dedicated_tenant_conn(tenant_id)
    try:
        # saver.setup() is run once by the migrator job, never here.
        graph = build_graph().compile(checkpointer=AsyncPostgresSaver(conn))
        snap = await graph.aget_state(config)
        if snap.values and not snap.next:
            return {"done": True, "status": snap.values.get("status")}  # already finished
        initial = (
            None
            if snap.values
            else {"tenant_id": str(tenant_id), "run_id": str(run_id), "domain": domain, "budget_usd": budget_usd}
        )
        try:
            async with asyncio.timeout(SLICE_SECONDS):
                await graph.ainvoke(initial, config)  # None => resume from last checkpoint
        except TimeoutError:
            return {"done": False}  # next slice resumes; ledger prevents re-billing
        except BudgetExceeded:
            return {"done": True, "status": "BUDGET_EXCEEDED"}
        final = await graph.aget_state(config)
        if final.next:  # paused on an interrupt (HITL gate)
            return {"done": False, "awaiting": True}
        return {"done": True, "status": final.values.get("status", "COMPLETED")}
    finally:
        await conn.close()
