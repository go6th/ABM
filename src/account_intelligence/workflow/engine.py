"""Build the LangGraph from workflow configuration, sequential in v1."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from account_intelligence.workflow.spec import WorkflowSpec, load_spec
from account_intelligence.workflow.stage import Stage, StageHandler
from account_intelligence.workflow.state import RunState


def build_graph(handlers: dict[str, StageHandler], spec: WorkflowSpec | None = None) -> Any:
    """Construct a sequential graph while retaining declared DAG dependencies."""
    workflow = spec or load_spec()
    if workflow.settings.get("execution_mode") != "sequential":
        raise ValueError("workflow v1 supports sequential execution only")
    graph = StateGraph(RunState)
    stage_by_id = {stage.id: stage for stage in workflow.stages}
    for stage_id in workflow.topological_order:
        stage = Stage(stage_by_id[stage_id], handlers.get(stage_id))
        graph.add_node(stage_id, stage.run)
    graph.add_edge(START, workflow.topological_order[0])
    for previous, following in zip(
        workflow.topological_order, workflow.topological_order[1:], strict=False
    ):
        graph.add_edge(previous, following)
    graph.add_edge(workflow.topological_order[-1], END)
    return graph
