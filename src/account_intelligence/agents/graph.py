"""Research graph: baseline, parallel branch fetches, distill."""

from __future__ import annotations

import asyncio
import hashlib
import json
import operator
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from account_intelligence.config import get_settings
from account_intelligence.connectors.base import CallContext, SystemClock
from account_intelligence.connectors.budget import BudgetDefaults
from account_intelligence.connectors.errors import BudgetExceeded as ConnectorBudgetExceeded
from account_intelligence.connectors.errors import (
    CallInFlight,
    ConnectorError,
    RetryableProviderError,
)
from account_intelligence.connectors.ledger import CallKey, Claimed, Ledger, Outcome, Replay
from account_intelligence.connectors.service import retry_policy_for
from account_intelligence.connectors.settings import load_providers_config
from account_intelligence.db.session import tenant_txn

GRAPH_VERSION = "2026-10-01.1"  # pinned on account_runs.graph_version at creation
BRANCHES = ["news", "jobs", "people", "tech"]
# One identity per worker process: the run lease owner. Immutable after import.
WORKER_ID = f"worker-{uuid4()}"
LEASE_TTL_S = 300
IN_FLIGHT_WAIT_S = 30.0
IN_FLIGHT_POLL_S = 0.05


class RunState(TypedDict, total=False):
    schema_version: int
    tenant_id: str
    run_id: str
    domain: str
    branch: str
    budget_usd: float
    spent_usd: Annotated[float, operator.add]  # parallel branches write it: needs a reducer
    facts: Annotated[list[dict[str, Any]], operator.add]  # reducer: branches append in parallel
    # Coverage outcomes written by parallel branches: a failed source is Blocked with a
    # reason and the run continues (skill rule 10).
    coverage: Annotated[list[dict[str, Any]], operator.add]
    status: str


class BudgetExceeded(Exception):
    pass


async def ledgered_call(
    tenant_id: str,
    run_id: str,
    tool: str,
    args: dict[str, Any],
    fn: Callable[..., Awaitable[dict[str, Any]]],
    *,
    ledger: Ledger | None = None,
    owner: str = WORKER_ID,
) -> dict[str, Any]:
    """Pay-once wrapper for a paid call whose JSON result is stored inline in the ledger.

    Runs on the connector ledger (lease + intent row, ADR-0006): identical concurrent
    calls in this worker pay once, a second worker on the run is refused, and a crash
    leaves a reconcilable ``started`` row. Broker calls go through
    ``account_intelligence.connectors.call_operation`` instead, which also charges the
    run budget; this wrapper does not (the graph's own guard does).
    """
    ledger = ledger or _default_ledger()
    ctx = CallContext(
        tenant_id=UUID(tenant_id), run_id=UUID(run_id), account_id=UUID(int=0), account_domain=""
    )
    digest = hashlib.sha256(
        json.dumps([tool, args], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    key = CallKey(args_hash=digest, tool=tool)
    waited = 0.0
    while True:
        claim = await ledger.claim(ctx, owner, key, 0.0, charge_budget=False)
        if isinstance(claim, Replay):
            if not isinstance(claim.response, dict):
                raise TypeError("stored tool response must be a JSON object")
            return claim.response
        if isinstance(claim, Claimed):
            break
        if waited >= IN_FLIGHT_WAIT_S:
            raise CallInFlight(attempt=claim.attempt)
        await asyncio.sleep(IN_FLIGHT_POLL_S)
        waited += IN_FLIGHT_POLL_S
    try:
        result = await fn(**args)  # the paid call (broker or LLM)
    except Exception as error:
        if isinstance(error, ConnectorError):
            error.attempt = claim.attempt  # the node compares it with its retry budget
        await ledger.finalize_failed(
            ctx,
            owner,
            key,
            claim,
            Outcome(charged_usd=0.0, error_class=type(error).__name__),
            charge_budget=False,
        )
        raise
    await ledger.finalize_done(
        ctx,
        owner,
        key,
        claim,
        Outcome(charged_usd=float(result.get("cost_usd", 0.0)), response=result),
        charge_budget=False,
    )
    return result


def _default_ledger() -> Ledger:
    settings = get_settings()
    return Ledger(
        tenant_txn,
        SystemClock(),
        lease_ttl_s=LEASE_TTL_S,
        budget_defaults=BudgetDefaults(settings.run_budget_usd, 60),
    )


async def baseline(state: RunState) -> dict[str, Any]:
    return {"schema_version": 1, "spent_usd": 0.0, "facts": []}


def fan_out(state: RunState) -> list[Send]:
    if state.get("spent_usd", 0.0) >= state["budget_usd"]:
        raise BudgetExceeded(state["run_id"])  # runner maps this to BUDGET_EXCEEDED
    return [Send("fetch_branch", {**state, "branch": b}) for b in BRANCHES]


def blocked_or_retry(error: ConnectorError, max_attempts: int) -> str | None:
    """The node-side decision for a connector failure (ADR-0007, one retry owner).

    Returns the Blocked reason, or None when the node RetryPolicy should retry: only a
    ``RetryableProviderError`` whose ledger attempt is below the retry budget is retried.
    """
    if isinstance(error, RetryableProviderError) and (error.attempt or 1) < max_attempts:
        return None
    return error.blocked_reason


async def fetch_branch(state: RunState) -> dict[str, Any]:
    async def broker(branch: str, domain: str) -> dict[str, Any]:
        raise NotImplementedError("call the allow-listed broker adapter here")

    try:
        out = await ledgered_call(
            state["tenant_id"],
            state["run_id"],
            f"broker:{state['branch']}",
            {"branch": state["branch"], "domain": state["domain"]},
            broker,
        )
    except ConnectorBudgetExceeded as error:
        raise BudgetExceeded(state["run_id"]) from error  # runner: BUDGET_EXCEEDED
    except ConnectorError as error:
        reason = blocked_or_retry(error, FETCH_RETRY.max_attempts)
        if reason is None:
            raise  # FETCH_RETRY retries the node
        return {"coverage": [{"branch": state["branch"], "status": "BLOCKED", "reason": reason}]}
    # untrusted text goes to the tool-less extractor tier, never to the planner
    return {
        "facts": out.get("facts", []),
        "spent_usd": out.get("cost_usd", 0.0),
        "coverage": [{"branch": state["branch"], "status": "DONE", "reason": None}],
    }


async def distill(state: RunState) -> dict[str, Any]:
    blocked = any(row["status"] == "BLOCKED" for row in state.get("coverage", []))
    return {"status": "PARTIAL" if blocked else "COMPLETED"}


# Broker 429/5xx are retried here and only here: 3 attempts, exponential backoff with
# jitter, then the branch is Blocked (skill reference 04). The connectors never retry.
FETCH_RETRY = retry_policy_for(load_providers_config())


def build_graph() -> StateGraph[RunState, None, RunState, RunState]:
    g = StateGraph(RunState)
    g.add_node("baseline", baseline)
    g.add_node(
        "fetch_branch",
        fetch_branch,
        retry_policy=FETCH_RETRY,
    )
    g.add_node("distill", distill)
    g.add_edge(START, "baseline")
    g.add_conditional_edges("baseline", fan_out, ["fetch_branch"])
    g.add_edge("fetch_branch", "distill")
    g.add_edge("distill", END)
    return g
