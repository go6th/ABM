"""The Distill agent (step 5): hypotheses, first-call questions and narrative from graded facts.

* the client audience sees public facts only (G10); the internal audience sees all;
* every claim must cite fact ids it was given (no claim without a fact id);
* a claim may not restate a grade (the documents show the engine's grade);
* both audiences are tenant-facing (the Internal Only view is read by the client's own staff), so
  the runtime scans every output for tool, provider and person names (G9) and rejects it whole;
  a claim that fails a fact or grade check is dropped and counted, the rest are kept.

GUARANTEE(test_distill_lies_are_dropped_or_rejected)
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
    known = {f.fact_id for f in visible}

    def keep(claims: tuple[Claim, ...], counts: Counter[str]) -> tuple[Claim, ...]:
        """Claims that cite known facts and do not restate a grade."""
        kept = []
        for claim in claims:
            if not set(claim.fact_ids) <= known:
                counts["unknown_fact"] += 1
            elif restates_grade(claim.text):
                counts["restates_grade"] += 1
            else:
                kept.append(claim)
        return tuple(kept)

    # LOOP(distill re-ask): cap=2 attempts; exit=a narrative survived, or the second attempt
    # (a narrative the model wrote and the guards dropped whole is asked for once more, with the
    # reasons; the Summary needs a narrative and used to fail its build without one)
    result = DistillResult(DistillOutput(), drops)
    for attempt in range(2):
        try:
            out = await call_agent(contract, model, meter, ctx, inp, client_bound=True)
        except OutputRejected as rejected:
            drops[f"output_{rejected.code}"] += 1
            return DistillResult(DistillOutput(), drops)
        assert isinstance(out, DistillOutput)
        counts: Counter[str] = Counter()
        kept = DistillOutput(
            hypotheses=keep(out.hypotheses, counts),
            questions=keep(out.questions, counts),
            narrative=keep(out.narrative, counts),
        )
        drops.update(counts)
        result = DistillResult(kept, drops)
        if kept.narrative or not out.narrative or attempt:
            break
        reasons = ", ".join(sorted(f"{k} ({v})" for k, v in counts.items()))
        inp = inp.model_copy(
            update={
                "notes": (
                    "Your previous narrative was rejected: " + reasons + ". Write each claim"
                    " without naming a grade or confidence level and cite only the fact_ids"
                    " given."
                )[:600]
            }
        )
        drops["narrative_reasked"] += 1
    return result
