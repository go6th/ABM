"""Build the LangGraph ``StateGraph`` from ``config/workflow_v1.yaml`` (sequential in v1).

* nodes run in topological order with concurrency 1; the declared ``depends_on`` DAG is kept in
  the spec so Phase 2 can add parallel edges (every multi-writer state key has a reducer).
* an ``approval_gate`` node follows ``setup``: when setup says approval is required and the run
  is not yet approved, it calls ``interrupt`` (the runner moves the run to AWAITING_APPROVAL and
  arms the 24 hour timeout; approval resumes the graph with ``Command(resume=True)``).
* every stage node carries the node ``RetryPolicy`` for retryable provider errors, the single
  retry owner for that failure class (W6); nothing else retries.
* ``on_enter(stage_id)`` lets the runner move the run to DISTILLING or VERIFYING as the graph
  reaches those stages.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, interrupt

from account_intelligence.workflow.spec import WorkflowSpec, load_spec
from account_intelligence.workflow.stage import Stage, StageHandler
from account_intelligence.workflow.state import RunState

APPROVAL_NODE = "approval_gate"
OnEnter = Callable[[str], Awaitable[None]]


async def approval_gate(state: RunState) -> dict[str, Any]:
    """Interrupt until approved when setup said approval is required (workflow spec 5, step 1)."""
    required = (state.get("references") or {}).get("approval") == "required"
    if not required or state.get("approved"):
        return {}
    decision = interrupt({"awaiting": "approval"})
    return {"approved": bool(decision)}


def _node(
    stage: Stage, on_enter: OnEnter | None
) -> Callable[[RunState], Awaitable[dict[str, Any]]]:
    async def run(state: RunState) -> dict[str, Any]:
        if on_enter is not None and stage.spec.id not in state.get("completed_stages", []):
            await on_enter(stage.spec.id)
        return await stage.run(state)

    return run


def build_graph(
    handlers: dict[str, StageHandler],
    spec: WorkflowSpec | None = None,
    *,
    retry_policy: RetryPolicy | None = None,
    on_enter: OnEnter | None = None,
) -> Any:
    """Construct the sequential graph (uncompiled)."""
    workflow = spec or load_spec()
    if workflow.settings.get("execution_mode") != "sequential":
        raise ValueError("workflow v1 supports sequential execution only")
    graph = StateGraph(RunState)
    by_id = {stage.id: stage for stage in workflow.stages}
    order = list(workflow.topological_order)
    for stage_id in order:
        graph.add_node(
            stage_id,
            cast(Any, _node(Stage(by_id[stage_id], handlers.get(stage_id)), on_enter)),
            retry_policy=retry_policy,
        )
    graph.add_node(APPROVAL_NODE, approval_gate)
    nodes = [order[0], APPROVAL_NODE, *order[1:]] if order[0] == "setup" else order
    graph.add_edge(START, nodes[0])
    for previous, following in zip(nodes, nodes[1:], strict=False):
        graph.add_edge(previous, following)
    graph.add_edge(nodes[-1], END)
    return graph
