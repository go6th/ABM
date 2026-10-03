"""Stages on the grading layer: ``verification`` (step 9) and ``distill_grade`` with the judge
pass (step 10, ADR-0005: verification before distill, judge after).

``verification``: the run's proposals are graded once WITHOUT persisting, to find the claims the
engine grades Lead. For each Lead (capped), the planner proposes differently worded queries
across tool categories (``lead_queries.v1``); they run through ``call_operation``; each is offered
to ``grading.leads.submit_query``, which accepts it only if it is a successful call of this run,
its category matches the ledger, and it is not a near duplicate (G9). The Lead states are stored
by reference and ``distill_grade`` grades with them. The ``lead_verification`` row is reported by
the verifier actor; it is Partial (``cap_hit``) unless every Lead is complete (T13).

``distill_grade``: grades every proposal (``grade_claims``) with the Lead states and the judge
port, persists facts through the single write path, drops and Lead records. Only counters enter
the state.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

from account_intelligence.agents.prompts import prompt
from account_intelligence.grading import repo
from account_intelligence.grading.adapters import LedgerAdapter
from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.integration import judge_profile_ok, load_run_info
from account_intelligence.grading.leads import (
    LeadVerification,
    QueryRecord,
    account_terms,
    evaluate_lead,
    mark_incomplete,
    new_lead,
    submit_query,
)
from account_intelligence.grading.pipeline import grade_claims, load_inputs
from account_intelligence.platform.ports import NotConfigured
from account_intelligence.tracker.model import Actor, ReasonCode
from account_intelligence.workflow.calls import execute, operations_for, validate_plan
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.proposals import MANIFEST, ProposalStore
from account_intelligence.workflow.spec import StageSpec
from account_intelligence.workflow.stages.research import ask_json
from account_intelligence.workflow.state import RunState

MAX_LEADS = 5
LEAD_ROW = "lead_verification"


def dump_leads(states: Mapping[str, LeadVerification]) -> bytes:
    """Lead states as JSON (stored by reference)."""
    return json.dumps(
        {k: dataclasses.asdict(v) for k, v in states.items()}, sort_keys=True
    ).encode()


def load_leads(blob: bytes) -> dict[str, LeadVerification]:
    """Inverse of ``dump_leads``."""
    out = {}
    for key, raw in json.loads(blob).items():
        queries = tuple(QueryRecord(**{**q, "tokens": tuple(q["tokens"])}) for q in raw["queries"])
        out[key] = LeadVerification(**{**raw, "queries": queries})
    return out


class LlmJudge:
    """``SupportJudgePort`` over the ``judge`` tier: sees only the claim and the sentence."""

    model_task = "verification"

    def __init__(self, deps: RunDeps) -> None:
        self.deps = deps

    async def judge(self, claim: str, sentence: str) -> Any:
        answer = await ask_json(
            self.deps,
            "judge",
            prompt("judge.v1"),
            {"claim": claim, "sentence": sentence},
            max_tokens=200,
        )
        if answer is None:
            raise NotConfigured("judge returned no JSON")
        return answer


class GradingStages:
    """Shared plumbing for the two grading stages."""

    def __init__(self, deps: RunDeps, spec_by_id: Mapping[str, StageSpec]) -> None:
        self.deps = deps
        self.spec = spec_by_id
        self.cfg = load_grading_config()
        self.store = ProposalStore(deps.payloads, deps.payload_key)

    def _adapter(self) -> LedgerAdapter:
        connectors = self.deps.connectors.connectors if self.deps.connectors is not None else {}
        return LedgerAdapter(self.deps.txn, self.deps.payloads, connectors, self.cfg)

    async def _proposals(self, state: RunState) -> tuple[str | None, list[Any]]:
        refs = state.get("references") or {}
        if not any(str(k).startswith("proposals:") for k in refs):
            return None, []
        manifest = await self.store.put_manifest(refs)
        return manifest, list(
            await self.store.load(self.deps.tenant_id, self.deps.run_id, manifest)
        )

    async def verification(self, state: RunState) -> dict[str, Any]:
        """Step 9 (see the module docstring)."""
        spec = self.spec["verification"]
        run = await load_run_info(self.deps.txn, self.deps.tenant_id, self.deps.run_id)
        _, proposals = await self._proposals(state)
        adapter = self._adapter()
        leads: dict[str, LeadVerification] = {}
        if proposals:
            inputs = await load_inputs(run, adapter, adapter)
            pre = await grade_claims(proposals, run, inputs, self.cfg, self.deps.clock.now())
            leads = {k: v for k, v in list(pre.leads.items())[:MAX_LEADS]}
        operations = operations_for(spec.allowed_tools)
        refs: list[str] = []
        budget = bool((state.get("counters") or {}).get("budget_exhausted", 0))
        terms = account_terms(run.account_domain, run.account_name)
        for key in sorted(leads):
            state_l = leads[key] or new_lead(key)
            for _ in range(spec.loop_cap or 3):
                if state_l.status != "pending" or budget:
                    break
                try:
                    plan = await ask_json(
                        self.deps,
                        "planner",
                        prompt("lead_queries.v1"),
                        {
                            "lead": key,
                            "accepted_queries": [q.text for q in state_l.accepted],
                            "operations": {op: cat for op, cat in sorted(operations.items())},
                        },
                    )
                except NotConfigured:
                    plan = None
                calls, _ = validate_plan(
                    {
                        "calls": [
                            {**c, "row": LEAD_ROW}
                            for c in (plan or {}).get("calls", [])
                            if isinstance(c, dict)
                        ]
                    },
                    [LEAD_ROW],
                    operations,
                    self.deps.domain,
                    3,
                )
                if not calls:
                    break
                batch = await execute(self.deps, calls, [LEAD_ROW])
                budget |= batch.budget_exhausted
                new_refs = batch.rows[LEAD_ROW].refs
                refs.extend(new_refs)
                fetches = await adapter.fetches(run)
                for ref in new_refs:
                    fetch = fetches.get(ref)
                    if fetch is None or state_l.status != "pending":
                        continue
                    for query in fetch.queries[:1]:
                        state_l = submit_query(
                            state_l, query, fetch.tool_category, ref, fetches, terms, self.cfg
                        )
            if budget and state_l.status == "pending":
                state_l = mark_incomplete(state_l, "budget")
            leads[key] = state_l
        complete = all(evaluate_lead(s, self.cfg) == "complete" for s in leads.values())
        references: dict[str, Any] = {}
        if leads:
            key = self.deps.payload_key("leads", "states.json")
            await self.deps.payloads.put(key, dump_leads(leads))
            references["lead_states"] = key
        async with self.deps.txn(self.deps.tenant_id) as session:
            tracker = self.deps.tracker(session)
            result = await tracker.record_outcome(
                self.deps.tenant_id,
                self.deps.run_id,
                LEAD_ROW,
                refs,
                Actor.STAGE,
                None if refs else (ReasonCode.BUDGET_CAP if budget else ReasonCode.NOT_PLANNED),
                stage="verification",
                cap_hit=not complete,
            )
        return {
            "coverage_reports": {LEAD_ROW: {"status": result.status.value}},
            "references": references,
            "counters": {
                "leads": len(leads),
                "leads_complete": sum(
                    evaluate_lead(s, self.cfg) == "complete" for s in leads.values()
                ),
            },
            "loop_exits": {"verification": "budget" if budget else "converged"},
        }

    async def distill_grade(self, state: RunState) -> dict[str, Any]:
        """Step 10: grade, judge, persist (single write path)."""
        run = await load_run_info(self.deps.txn, self.deps.tenant_id, self.deps.run_id)
        manifest, proposals = await self._proposals(state)
        if manifest is None:
            return {
                "coverage_reports": {},
                "counters": {"claims_graded": 0},
                "loop_exits": {"distill_grade": "no_progress"},
            }
        adapter = self._adapter()
        lead_ref = (state.get("references") or {}).get("lead_states")
        leads = load_leads(await self.deps.payloads.get(lead_ref)) if lead_ref else {}
        await repo.pin_config(self.deps.txn, run, self.cfg.content_hash)
        inputs = await load_inputs(run, adapter, adapter)
        judge = LlmJudge(self.deps)
        result = await grade_claims(
            proposals,
            run,
            inputs,
            self.cfg,
            self.deps.clock.now(),
            judge=judge,
            judge_profile_ok=judge_profile_ok(self.cfg),
            lead_states=leads,
        )
        for claim in result.graded:
            await repo.record_graded_fact(self.deps.txn, run, claim)
        await repo.record_drops(self.deps.txn, run, result.dropped)
        for lead in result.leads.values():
            await repo.record_lead(self.deps.txn, run, lead)
        counters = {"claims_graded": len(result.graded), "claims_dropped": len(result.dropped)}
        counters.update({f"grade_{k}": v for k, v in result.grade_counts.items()})
        return {
            "coverage_reports": {},
            "references": {MANIFEST: manifest},
            "counters": counters,
            "loop_exits": {"distill_grade": "converged"},
        }
