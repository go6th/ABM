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
* ``execution_mode: parallel`` (Parallel execution v1): after the approval gate one ``research``
  node runs the research stages as units on the dependency-aware scheduler
  (``workflow.parallel_phase``), a ``cap_gate`` node waits for a pause decision (D-45), then the
  sequential tail (distill, build, gate, deliver) follows unchanged. The research node carries the
  phase ``RetryPolicy`` (still the single retry owner; built in ``connectors.service``).
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
    research: Callable[[RunState], Awaitable[dict[str, Any]]] | None = None,
    phase_retry_policy: RetryPolicy | None = None,
) -> Any:
    """Construct the graph (uncompiled): sequential, or parallel with a research node."""
    workflow = spec or load_spec()
    mode = workflow.settings.get("execution_mode")
    if mode == "parallel":
        if research is None:
            raise ValueError("parallel execution needs the research node")
        return _parallel_graph(
            handlers, workflow, retry_policy, phase_retry_policy, on_enter, research
        )
    if mode != "sequential":
        raise ValueError(f"unknown execution_mode {mode!r}")
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


def _parallel_graph(
    handlers: dict[str, StageHandler],
    workflow: WorkflowSpec,
    retry_policy: RetryPolicy | None,
    phase_retry_policy: RetryPolicy | None,
    on_enter: OnEnter | None,
    research: Callable[[RunState], Awaitable[dict[str, Any]]],
) -> Any:
    """setup, approval gate, research (with the cap gate loop), then the sequential tail."""
    from account_intelligence.workflow.parallel_phase import (
        CAP_GATE_NODE,
        RESEARCH_NODE,
        after_research,
        cap_gate,
        research_stages,
    )

    by_id = {stage.id: stage for stage in workflow.stages}
    phase = set(research_stages())
    unknown = phase - by_id.keys()
    if unknown:
        raise ValueError(f"parallel bodies name unknown stages: {sorted(unknown)}")
    tail = [s for s in workflow.topological_order if s not in phase and s != "setup"]
    if not tail or "setup" not in by_id:
        raise ValueError("parallel execution needs setup and a sequential tail")
    graph = StateGraph(RunState)
    graph.add_node(
        "setup",
        cast(Any, _node(Stage(by_id["setup"], handlers.get("setup")), on_enter)),
        retry_policy=retry_policy,
    )
    graph.add_node(APPROVAL_NODE, approval_gate)
    graph.add_node(RESEARCH_NODE, cast(Any, research), retry_policy=phase_retry_policy)
    graph.add_node(CAP_GATE_NODE, cap_gate)
    for stage_id in tail:
        graph.add_node(
            stage_id,
            cast(Any, _node(Stage(by_id[stage_id], handlers.get(stage_id)), on_enter)),
            retry_policy=retry_policy,
        )
    graph.add_edge(START, "setup")
    graph.add_edge("setup", APPROVAL_NODE)
    graph.add_edge(APPROVAL_NODE, RESEARCH_NODE)
    graph.add_conditional_edges(
        RESEARCH_NODE, after_research, {CAP_GATE_NODE: CAP_GATE_NODE, "next": tail[0]}
    )
    graph.add_edge(CAP_GATE_NODE, RESEARCH_NODE)
    for previous, following in zip(tail, tail[1:], strict=False):
        graph.add_edge(previous, following)
    graph.add_edge(tail[-1], END)
    return graph
