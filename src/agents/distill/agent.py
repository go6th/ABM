"""The Distill agent (step 5): hypotheses, first-call questions and narrative from graded facts.

* the client audience sees public facts only (G10); the internal audience sees all;
* every claim must cite fact ids it was given (no claim without a fact id);
* a claim may not restate a grade (the documents show the engine's grade);
* a narrative sentence may state only facts the grading policy lets a client file state
  (Confirmed); one resting on any other fact must be a question, or it is dropped and counted
  (``unconfirmed_stated``). Before 2026-10-15 such sentences passed here and were withheld later,
  silently, so the Summary had no narrative and the whole build failed (``missing_narrative``);
* both audiences are tenant-facing (the Internal Only view is read by the client's own staff), so
  the runtime scans every output for tool, provider and person names (G9) and rejects it whole;
  a claim that fails a fact or grade check is dropped and counted, the rest are kept;
* a narrative that is still empty after the one re-ask is made of the first-call questions
  (question-form sentences citing the same facts): the Summary always has an honest opening,
  and what may not be stated is asked (PRD: a Probable, Hypothesis or Lead "becomes a question").

GUARANTEE(test_distill_lies_are_dropped_or_rejected)
GUARANTEE(test_a_narrative_that_states_an_unconfirmed_fact_becomes_a_question)
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from functools import cache

from agents.contracts import AgentContract, BudgetMeter
from agents.errors import OutputRejected
from agents.guards import Role, for_audience, restates_grade
from agents.ports import AgentModel, RunContext
from agents.runtime import call_agent
from agents.schemas import Claim, DistillInput, DistillOutput, StoredFact
from exports.config import ContentRules, load_export_config
from exports.policy import authored_violations

# the longest narrative made from questions when no narrative sentence survived
NARRATIVE_FROM_QUESTIONS = 8


@cache
def _content_rules() -> ContentRules:
    """The export content rules (D-24 prose rules), read once: the same rules the Summary is
    checked against, so a claim the files would withhold is dropped and re-asked here, while
    the model is still in the loop (staging 2026-10-21: a dash in finished prose failed the
    build after the research was paid for)."""
    return load_export_config().rules


def prose_problem(text: str) -> str | None:
    """The first D-24 prose rule a claim breaks (``banned_dash``, ``stale_phrase``...), or None.
    Partner framings are not checked here (the run does not know the tenant's partner status);
    the Summary withholds them."""
    found = authored_violations(text, _content_rules(), "distill", is_partner=False)
    return found[0].code if found else None


@dataclass
class DistillResult:
    """The kept output and the counted drops."""

    output: DistillOutput
    drops: Counter[str] = field(default_factory=Counter)


def states_unstatable(claim: Claim, statable: Collection[str]) -> bool:
    """A narrative sentence that is not a question and rests on a fact that may not be stated."""
    if claim.text.rstrip().endswith("?"):
        return False
    return any(fact_id not in statable for fact_id in claim.fact_ids)


async def distill(
    contract: AgentContract,
    model: AgentModel,
    meter: BudgetMeter,
    ctx: RunContext,
    facts: tuple[StoredFact, ...],
    audience: Role,
    statable: Collection[str] | None = None,
) -> DistillResult:
    """See the module docstring. ``statable`` is the fact ids a narrative may state; None means
    the facts graded ``confirmed``."""
    visible = for_audience(facts, audience)
    allowed = (
        frozenset(statable)
        if statable is not None
        else frozenset(f.fact_id for f in visible if f.grade == "confirmed")
    )
    inp = DistillInput(
        account_name=ctx.account_name,
        audience="client" if audience == "public" else "internal",
        facts=visible,
        statable_fact_ids=tuple(sorted(allowed & {f.fact_id for f in visible})),
    )
    drops: Counter[str] = Counter()
    if not visible:
        return DistillResult(DistillOutput(), drops)
    known = {f.fact_id for f in visible}

    def keep(
        claims: tuple[Claim, ...], counts: Counter[str], *, narrative: bool = False
    ) -> tuple[Claim, ...]:
        """Claims that cite known facts, do not restate a grade and (narrative) state only what
        may be stated."""
        kept = []
        for claim in claims:
            if not set(claim.fact_ids) <= known:
                counts["unknown_fact"] += 1
            elif restates_grade(claim.text):
                counts["restates_grade"] += 1
            elif narrative and states_unstatable(claim, allowed):
                counts["unconfirmed_stated"] += 1
            elif (problem := prose_problem(claim.text)) is not None:
                counts[problem] += 1
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
            narrative=keep(out.narrative, counts, narrative=True),
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
                    " without naming a grade or confidence level, cite only the fact_ids given,"
                    " and state only facts in statable_fact_ids; write every other sentence as"
                    " a question ending with a question mark. Use no em dash, en dash or"
                    " spaced hyphen as punctuation."
                )[:600]
            }
        )
        drops["narrative_reasked"] += 1
    if not result.output.narrative and result.output.questions:
        # the honest opening: what may not be stated is asked (PRD "becomes a question")
        result.output = result.output.model_copy(
            update={"narrative": result.output.questions[:NARRATIVE_FROM_QUESTIONS]}
        )
        drops["narrative_from_questions"] += 1
    return result
