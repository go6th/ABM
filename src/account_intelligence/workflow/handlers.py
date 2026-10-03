"""The production handler for every Workflow v1 stage, bound to one run's dependencies.

This is the single place that maps stage ids to code; ``engine.build_graph`` takes the result.
Relationship rows that need the client history port (Phase B) are not researched: they end Not
done with ``waiting_on_account_owner`` when the run has no pursuing client (recorded once, never
asked again), otherwise ``not_available_yet``. Nothing is guessed.
"""

from __future__ import annotations

from typing import Any

from account_intelligence.tracker.config import load_checklist
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.stage import StageHandler
from account_intelligence.workflow.stages import core
from account_intelligence.workflow.stages.grading_stages import GradingStages
from account_intelligence.workflow.stages.research import ResearchStage
from account_intelligence.workflow.state import RunState

CLIENT_HISTORY_ROWS = ("prior_customer_status", "connections_alumni", "customers_partners_matched")
RESEARCH_PREFIX = {
    "pass1_relationship": "relationship",
    "baseline": "baseline",
    "news": "news",
    "tech": "tech",
    "people": "people",
    "name_searches": "content",
}


def _bind(fn: Any, deps: RunDeps) -> StageHandler:
    async def handler(state: RunState) -> dict[str, Any]:
        result: dict[str, Any] = await fn(deps, state)
        return result

    return handler


def people_operations() -> frozenset[str]:
    """Operations of the active people source (P0-3 switch, ``config/people_sources.yaml``)."""
    import yaml

    from account_intelligence.config import CONFIG_DIR

    data = yaml.safe_load((CONFIG_DIR / "people_sources.yaml").read_text())
    return frozenset(data["sources"][data["active"]]["operations"])


def build_handlers(deps: RunDeps) -> dict[str, StageHandler]:
    """Every stage id in ``config/workflow_v1.yaml`` mapped to its production handler."""
    labels = {r.key: r.label for r in load_checklist().rows}
    specs = {s.id: s for s in deps.spec.stages}
    reason = (
        "not_available_yet" if deps.run_input.get("pursuing_client") else "waiting_on_account_owner"
    )
    handlers: dict[str, StageHandler] = {}
    for stage_id, prefix in RESEARCH_PREFIX.items():
        fixed = (
            {row: reason for row in CLIENT_HISTORY_ROWS if row in specs[stage_id].coverage}
            if stage_id == "pass1_relationship"
            else {}
        )
        only = people_operations() if stage_id == "people" else None
        handlers[stage_id] = ResearchStage(specs[stage_id], deps, labels, prefix, fixed, only)
    grading = GradingStages(deps, specs)
    handlers["setup"] = _bind(core.setup, deps)
    handlers["jobs"] = _bind(core.jobs_stage, deps)
    handlers["verification"] = grading.verification
    handlers["distill_grade"] = grading.distill_grade
    handlers["build"] = _bind(core.build, deps)
    handlers["gate"] = _bind(core.gate, deps)
    handlers["deliver"] = _bind(core.deliver, deps)
    missing = set(specs) - set(handlers)
    if missing:  # pragma: no cover - guarded by tests/system/test_wiring.py
        raise RuntimeError(f"stages without a handler: {sorted(missing)}")
    return handlers
