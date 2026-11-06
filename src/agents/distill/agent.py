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
* the Summary's section 1 parts after the picture (2026-10-26, the reference Summary): what
  stands out and the simplest way to sharpen it follow the narrative rule above; a lane meaning
  also names the lane of a fact it cites and labels its interpretation ("Our interpretation:");
  how this could be wrong is about the evidence itself and follows the claim checks only;
* a figure in a claim must be written as in the value or event date of a fact the claim cites
  (``unregistered_number``, the same B6 rule the Summary applies over the cited rows): a run on
  staging lost all of "what stands out" and two caveats to figures the model worked out itself
  (2026-11-04); a claim that breaks a rule the model can mend is re-asked once, with the reasons;
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
from agents.schemas import Claim, DistillInput, DistillOutput, LaneClaim, StoredFact
from exports.config import ContentRules, default_export_config
from exports.numbers import unregistered
from exports.policy import authored_violations

# the longest narrative made from questions when no narrative sentence survived
NARRATIVE_FROM_QUESTIONS = 8
# the words a lane meaning labels its interpretation with (the reference Summary, section 1)
INTERPRETATION_LABEL = "Our interpretation:"


@cache
def _content_rules() -> ContentRules:
    """The export content rules (D-24 prose rules), read once: the same rules the Summary is
    checked against, so a claim the files would withhold is dropped and re-asked here, while
    the model is still in the loop (staging 2026-10-21: a dash in finished prose failed the
    build after the research was paid for)."""
    return default_export_config().rules


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


def _kept(out: DistillOutput) -> int:
    """How many claims an output holds across its parts."""
    return sum(len(getattr(out, name)) for name in type(out).model_fields)


def unregistered_figure(claim: Claim, cited: Collection[str]) -> bool:
    """True when the claim carries a figure that none of its cited facts' texts (value, event
    date) carries (B6 over the cited rows; the Summary applies the same rule over the rendered
    rows, so a claim that passes here is printed, one that fails here would be withheld there)."""
    return bool(unregistered(claim.text, frozenset(), cited))


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
    focus: str | None = None,
) -> DistillResult:
    """See the module docstring. ``statable`` is the fact ids a narrative may state; None means
    the facts graded ``confirmed``. ``focus`` is the research goal the user wrote (the narrative
    leads with the facts relevant to it)."""
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
        focus=" ".join((focus or "").split())[:500] or None,
    )
    drops: Counter[str] = Counter()
    if not visible:
        return DistillResult(DistillOutput(), drops)
    known = {f.fact_id for f in visible}
    lane_of = {f.fact_id: f.lane for f in visible}
    figure_texts = {f.fact_id: tuple(t for t in (f.value, f.event_date) if t) for f in visible}

    def cited_texts(claim: Claim) -> list[str]:
        """The figures (values and dates) of the facts the claim cites."""
        return [t for fid in claim.fact_ids for t in figure_texts.get(fid, ())]

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
            elif unregistered_figure(claim, cited_texts(claim)):
                counts["unregistered_number"] += 1
            else:
                kept.append(claim)
        return tuple(kept)

    def keep_lanes(claims: tuple[LaneClaim, ...], counts: Counter[str]) -> tuple[LaneClaim, ...]:
        """Lane meanings that pass the narrative checks, name the lane of a fact they cite and
        label their interpretation (the reference Summary: "Our interpretation: ...")."""
        passed = {id(c) for c in keep(claims, counts, narrative=True)}
        kept = []
        for claim in claims:
            if id(claim) not in passed:
                continue
            if claim.lane not in {lane_of.get(fid) for fid in claim.fact_ids}:
                counts["lane_not_cited"] += 1
            elif INTERPRETATION_LABEL.lower() not in claim.text.lower():
                counts["interpretation_unlabeled"] += 1
            else:
                kept.append(claim)
        return tuple(kept)

    # LOOP(distill re-ask): cap=2 attempts; exit=a narrative survived with every figure in
    # place, or the second attempt (a narrative the guards dropped whole, or a claim cut for a
    # figure the cited facts do not carry, is asked for once more with the reasons; the attempt
    # that keeps a narrative and the most claims is the result, so a re-ask never leaves the
    # Summary with less than the first answer gave; every attempt's drops are counted)
    attempts: list[DistillOutput] = []
    for attempt in range(2):
        try:
            out = await call_agent(contract, model, meter, ctx, inp, client_bound=True)
        except OutputRejected as rejected:
            drops[f"output_{rejected.code}"] += 1
            if not attempts:
                return DistillResult(DistillOutput(), drops)
            break
        assert isinstance(out, DistillOutput)
        counts: Counter[str] = Counter()
        kept = DistillOutput(
            hypotheses=keep(out.hypotheses, counts),
            questions=keep(out.questions, counts),
            narrative=keep(out.narrative, counts, narrative=True),
            stands_out=keep(out.stands_out, counts, narrative=True),
            lane_meanings=keep_lanes(out.lane_meanings, counts),
            could_be_wrong=keep(out.could_be_wrong, counts),
            sharpen=keep(out.sharpen, counts, narrative=True),
        )
        drops.update(counts)
        attempts.append(kept)
        whole = not kept.narrative and bool(out.narrative)
        if attempt or not (whole or counts["unregistered_number"]):
            break
        reasons = ", ".join(sorted(f"{k} ({v})" for k, v in counts.items()))[:200]
        inp = inp.model_copy(
            update={
                "notes": (
                    f"Your previous answer was rejected in part, {sum(counts.values())} claims"
                    f" lost: {reasons}."
                    " Cite only the fact_ids given; state only facts in statable_fact_ids and"
                    " write every other sentence as a question ending with a question mark;"
                    " write a figure only exactly as it stands in the value or event_date of a"
                    " fact you cite, else leave the figure out; name no grade or confidence"
                    " level; use no em dash, en dash or spaced hyphen as punctuation."
                )[:600]
            }
        )
        drops["narrative_reasked" if whole else "claims_reasked"] += 1
    chosen = max(attempts, key=lambda a: (bool(a.narrative), _kept(a)))
    result = DistillResult(chosen, drops)
    if not result.output.narrative and result.output.questions:
        # the honest opening: what may not be stated is asked (PRD "becomes a question")
        result.output = result.output.model_copy(
            update={"narrative": result.output.questions[:NARRATIVE_FROM_QUESTIONS]}
        )
        drops["narrative_from_questions"] += 1
    return result
