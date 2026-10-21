"""src/agents/graph.py - research graph (reduced to the load-bearing structure)."""

from __future__ import annotations

import hashlib
import json
import operator
from typing import Annotated, Any, Awaitable, Callable, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send
from sqlalchemy import text

from src.db.session import tenant_txn

GRAPH_VERSION = "2026-10-01.1"  # pinned on account_runs.graph_version at creation
BRANCHES = ["news", "jobs", "people", "tech"]


class RunState(TypedDict, total=False):
    schema_version: int
    tenant_id: str
    run_id: str
    domain: str
    budget_usd: float
    spent_usd: Annotated[float, operator.add]  # FIX (defect 1): parallel branches write it; needs a reducer
    facts: Annotated[list[dict[str, Any]], operator.add]  # reducer: branches append in parallel
    status: str


class BudgetExceeded(Exception):
    pass


async def ledgered_call(
    tenant_id: str, run_id: str, tool: str, args: dict[str, Any], fn: Callable[..., Awaitable[dict[str, Any]]]
) -> dict[str, Any]:
    """Exactly-once *billing* for paid calls: replay from the ledger if this call already ran."""
    h = hashlib.sha256(json.dumps([tool, args], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    async with tenant_txn(UUID(tenant_id)) as s:
        row = (
            await s.execute(
                text("SELECT response FROM tool_calls WHERE run_id = :r AND args_hash = :h"), {"r": run_id, "h": h}
            )
        ).first()
        if row:
            return row.response
    result = await fn(**args)  # the paid call (broker or LLM)
    async with tenant_txn(UUID(tenant_id)) as s:
        await s.execute(
            text(
                "INSERT INTO tool_calls (tenant_id, run_id, tool, args_hash, response, cost_usd) "
                "VALUES ((SELECT app_tenant()), :r, :t, :h, CAST(:resp AS jsonb), :c) "
                "ON CONFLICT (run_id, args_hash) DO NOTHING"
            ),
            {"r": run_id, "t": tool, "h": h, "resp": json.dumps(result), "c": result.get("cost_usd", 0)},
        )
    return result


async def baseline(state: RunState) -> dict[str, Any]:
    return {"schema_version": 1, "spent_usd": 0.0, "facts": []}


def fan_out(state: RunState) -> list[Send]:
    if state.get("spent_usd", 0.0) >= state["budget_usd"]:
        raise BudgetExceeded(state["run_id"])  # runner maps this to BUDGET_EXCEEDED
    return [Send("fetch_branch", {**state, "branch": b}) for b in BRANCHES]


async def fetch_branch(state: dict[str, Any]) -> dict[str, Any]:
    async def broker(branch: str, domain: str) -> dict[str, Any]:
        raise NotImplementedError("call the allow-listed broker adapter here")

    out = await ledgered_call(
        state["tenant_id"],
        state["run_id"],
        f"broker:{state['branch']}",
        {"branch": state["branch"], "domain": state["domain"]},
        broker,
    )
    # untrusted text goes to the tool-less extractor tier, never to the planner
    return {"facts": out.get("facts", []), "spent_usd": out.get("cost_usd", 0.0)}


async def distill(state: RunState) -> dict[str, Any]:
    return {"status": "COMPLETED"}


def build_graph() -> StateGraph:
    g = StateGraph(RunState)
    g.add_node("baseline", baseline)
    g.add_node(
        "fetch_branch", fetch_branch, retry_policy=RetryPolicy(max_attempts=3, initial_interval=2.0, backoff_factor=2.0)
    )
    g.add_node("distill", distill)
    g.add_edge(START, "baseline")
    g.add_conditional_edges("baseline", fan_out, ["fetch_branch"])
    g.add_edge("fetch_branch", "distill")
    g.add_edge("distill", END)
    return g
