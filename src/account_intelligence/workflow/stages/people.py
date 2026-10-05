"""Step 7 (People) through the people pipeline: a thin adapter (``src/people`` owns the logic).

The pipeline owns ``people_by_platform``, ``full_profiles_senior_people`` and
``posts_priority_people``: it hands their evidence to the tracker and never writes a status. Any
other row the stage owns in ``config/workflow_v1.yaml`` (``employee_content_github``, a proposed
owner) stays with the generic research stage, restricted to that row. A re-entered stage replays
the stored record (zero paid calls). ``top_people`` is exposed for the name-searches stage.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sqlalchemy import text

from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.state import RunState
from people.pipeline import OWNED_ROWS, PipelineResult

STAGE = "people"


def _is_hostile() -> Any:
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.grading.errors import HostileContent
    from account_intelligence.grading.spans import check_hostile

    gcfg = load_grading_config()

    def hostile(body: str) -> bool:
        """The grading layer's one hostile-content check (G14)."""
        try:
            check_hostile(body, gcfg)
        except HostileContent:
            return True
        return False

    return hostile, tuple(gcfg.spans.instruction_markers)


async def run_people_stage(deps: RunDeps, operations: frozenset[str]) -> tuple[PipelineResult, Any]:
    """Build the production ports and run the pipeline once for this run."""
    import time

    from account_intelligence.workflow.proposals import ProposalStore
    from people.adapters import (
        ConnectorPeopleSearch,
        LedgerCosts,
        LlmPeopleExtractor,
        NoMentions,
        PeopleProposalSink,
        PeopleTrackerEvidence,
        RunPeopleInput,
        SourceRegistry,
        UnavailablePosts,
        UnavailableProfiles,
    )
    from people.config import load_people_config
    from people.pipeline import PeoplePipeline
    from people.repo import PgPeopleRepo
    from people.stage import run_people

    cfg = load_people_config()
    async with deps.txn(deps.tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT run_input, created_at, budget_usd FROM account_runs WHERE id = :r"),
                {"r": deps.run_id},
            )
        ).one()
    share = next(s.budget_share for s in deps.spec.stages if s.id == STAGE)
    budget = float(row.budget_usd) * share if row.budget_usd is not None else None
    hostile, markers = _is_hostile()
    repo = PgPeopleRepo(deps.txn, deps.tenant_id, deps.account_id, deps.payloads, deps.payload_key)
    mentions = NoMentions()
    deadline = deps.deadline
    pipeline = PeoplePipeline(
        cfg,
        ConnectorPeopleSearch(deps, operations, cfg),
        mentions,
        mentions,
        UnavailableProfiles(),
        UnavailablePosts(),
        LedgerCosts(deps),
        LlmPeopleExtractor(deps),
        repo,
        canary=deps.canary,
        hostile_markers=markers,
        is_hostile=hostile,
        out_of_time=lambda: deadline is not None and time.monotonic() >= deadline,
    )
    sink = PeopleProposalSink(ProposalStore(deps.payloads, deps.payload_key))
    result = await run_people(
        str(deps.run_id),
        deps.domain,
        pipeline,
        repo,
        SourceRegistry(deps),
        PeopleTrackerEvidence(deps),
        sink,
        RunPeopleInput(dict(row.run_input or {}), row.created_at.date(), budget),
    )
    return result, sink


async def people_stage(
    deps: RunDeps, state: RunState, research: Any | None = None
) -> dict[str, Any]:
    """The workflow handler: the people pipeline, then the generic stage for any other row."""
    from account_intelligence.workflow.handlers import people_operations

    result, sink = await run_people_stage(deps, people_operations())
    out: dict[str, Any] = {
        "coverage_reports": {},
        "references": {f"proposals:{STAGE}": sink.reference} if sink.reference else {},
        "counters": {"tool_calls": result.calls_made, "people_found": len(result.people)},
        "loop_exits": {f"people_{k}": v for k, v in sorted(result.exit_reasons.items())},
    }
    if research is not None:
        rest = await research(state)
        for key in ("coverage_reports", "references", "loop_exits"):
            out[key] = {**rest.get(key, {}), **out[key]}
        counters = dict(rest.get("counters", {}))
        counters["tool_calls"] = counters.get("tool_calls", 0) + out["counters"]["tool_calls"]
        out["counters"] = {**counters, "people_found": len(result.people)}
    async with deps.txn(deps.tenant_id) as session:
        rows = await deps.tracker(session).repo.get_all_rows(deps.run_id)
    for r in rows:
        if r.key in OWNED_ROWS:
            out["coverage_reports"][r.key] = {"status": r.status.value if r.status else "unset"}
    return out


def research_spec_without_owned_rows(spec: Any) -> Any:
    """The stage spec restricted to the rows the people pipeline does not own."""
    return replace(spec, coverage=tuple(r for r in spec.coverage if r not in OWNED_ROWS))
