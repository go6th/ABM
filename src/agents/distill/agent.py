"""The Distill agent (step 5): hypotheses, first-call questions and narrative from graded facts.

* the client audience sees public facts only (G10); the internal audience sees all;
* every claim must cite fact ids it was given (no claim without a fact id);
* a claim may not restate a grade (the documents show the engine's grade);
* both audiences are tenant-facing (the Internal Only view is read by the client's own staff), so
  the runtime scans every output for tool, provider and person names (G9) and rejects it whole;
  a claim that fails a fact or grade check is dropped and counted, the rest are kept.

GUARANTEE(test_distill_claims_need_known_fact_ids_and_no_grade_words)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from agents.contracts import AgentContract, BudgetMeter
from agents.errors import OutputRejected
from agents.guards import Role, for_audience, restates_grade
from agents.ports import AgentModel, RunContext
from agents.runtime import call_agent
from agents.schemas import Claim, DistillInput, DistillOutput, StoredFact


@dataclass
class DistillResult:
    """The kept output and the counted drops."""

    output: DistillOutput
    drops: Counter[str] = field(default_factory=Counter)


async def distill(
    contract: AgentContract,
    model: AgentModel,
    meter: BudgetMeter,
    ctx: RunContext,
    facts: tuple[StoredFact, ...],
    audience: Role,
) -> DistillResult:
    """See the module docstring."""
    visible = for_audience(facts, audience)
    inp = DistillInput(
        account_name=ctx.account_name,
        audience="client" if audience == "public" else "internal",
        facts=visible,
    )
    drops: Counter[str] = Counter()
    if not visible:
        return DistillResult(DistillOutput(), drops)
    try:
        out = await call_agent(contract, model, meter, ctx, inp, client_bound=True)
    except OutputRejected as rejected:
        drops[f"output_{rejected.code}"] += 1
        return DistillResult(DistillOutput(), drops)
    assert isinstance(out, DistillOutput)
    known = {f.fact_id for f in visible}

    def keep(claims: tuple[Claim, ...]) -> tuple[Claim, ...]:
        kept = []
        for claim in claims:
            if not set(claim.fact_ids) <= known:
                drops["unknown_fact"] += 1
            elif restates_grade(claim.text):
                drops["restates_grade"] += 1
            else:
                kept.append(claim)
        return tuple(kept)

    return DistillResult(
        DistillOutput(
            hypotheses=keep(out.hypotheses),
            questions=keep(out.questions),
            narrative=keep(out.narrative),
        ),
        drops,
    )
