"""Interpretation of stored pulls (Phase 2): the Pass 1, Pass 2 and People agents (steps 2 to 4).

One model call per source group (step and route need) reads only that group's stored payloads
(quarantined text) and proposes candidates. Code then decides what survives:

* every candidate's row must belong to the group and be the row of every payload it quotes; its
  lane must be a skill lane (or none);
* G4: its evidence must cite payloads this call was given and quote them verbatim;
* G2: a declared keyword match or date range stays one, and a payload code knows is a match makes
  the candidate one whatever the model claims; "read this run" needs full-page reads (the grading
  engine decides Confirmed itself and grades an unread match Lead);
* private payloads make the candidate private (G10);
* people: the title, and any tenure, must be quoted in the evidence (no invented titles);
* G3: a lead-drop request is honoured only with three phrasings across two tool categories.

Drops are counted by reason; nothing is silently lost.

GUARANTEE(test_fabricated_quotes_and_urls_never_become_facts)
GUARANTEE(test_invented_titles_names_and_tenure_are_dropped)
GUARANTEE(test_promoted_lead_from_a_search_snippet_stays_a_lead)
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from agents.config import SkillConfig
from agents.contracts import AgentContract, BudgetMeter
from agents.errors import OutputRejected
from agents.guards import DropDecision, evidence_problem, lead_drop_decision, quoted
from agents.ports import AgentModel, GradingCandidate, RunContext
from agents.runtime import call_agent
from agents.schemas import (
    ExtractionInput,
    ExtractionOutput,
    FactCandidate,
    PayloadView,
    PeopleOutput,
    PersonCandidate,
)

FULL_READ_NEEDS = frozenset({"page_read", "scrape"})


@dataclass
class ExtractionResult:
    """What survived one call, and why the rest did not."""

    candidates: list[GradingCandidate] = field(default_factory=list)
    people: list[PersonCandidate] = field(default_factory=list)
    lead_drops: dict[str, DropDecision] = field(default_factory=dict)
    drops: Counter[str] = field(default_factory=Counter)


def _origin(
    candidate: FactCandidate, given: Mapping[str, PayloadView], cfg: SkillConfig
) -> tuple[str, bool]:
    """``(origin, read_this_run)``. A declared match stays a match; a payload code knows is a
    date range or keyword match makes the candidate one whatever the model claims; it was read
    this run only when every evidence payload is a full-page read (the engine checks again)."""
    payloads = [given[e.payload_ref] for e in candidate.evidence]
    full_reads = all(cfg.plan[p.row_key].need in FULL_READ_NEEDS for p in payloads)
    floor = next((p.match_kind for p in payloads if p.match_kind), None)
    origin = floor if candidate.origin == "read" and floor else candidate.origin
    return origin, full_reads and floor is None


async def extract(
    contract: AgentContract,
    model: AgentModel,
    meter: BudgetMeter,
    ctx: RunContext,
    inp: ExtractionInput,
    cfg: SkillConfig,
) -> ExtractionResult:
    """Run one extraction call and filter its output (see the module docstring)."""
    result = ExtractionResult()
    if not inp.payloads:
        return result
    try:
        out = await call_agent(contract, model, meter, ctx, inp, client_bound=False)
    except OutputRejected as rejected:
        result.drops[f"output_{rejected.code}"] += 1
        return result
    given = {p.payload_ref: p for p in inp.payloads}
    rows = {p.row_key for p in inp.payloads}
    if isinstance(out, PeopleOutput):
        _people(out, given, result)
        return result
    assert isinstance(out, ExtractionOutput)
    for c in out.candidates:
        problem = _candidate_problem(c, given, rows, cfg)
        if problem:
            result.drops[problem] += 1
            continue
        origin, read = _origin(c, given, cfg)
        evidence_payloads = [given[e.payload_ref] for e in c.evidence]
        private = any(p.visibility == "private" for p in evidence_payloads)
        result.candidates.append(
            GradingCandidate(
                candidate=c.model_copy(update={"origin": origin}),
                urls=tuple(dict.fromkeys(p.url for p in evidence_payloads)),
                visibility="private" if private else "public",
                read_this_run=read,
            )
        )
    for drop in out.lead_drops:
        decision = lead_drop_decision(drop.attempts, cfg.min_phrasings, cfg.min_tool_categories)
        result.lead_drops[drop.lead_key] = decision
        if not decision.allowed:
            result.drops[f"lead_kept_{decision.reason}"] += 1
    return result


def _candidate_problem(
    c: FactCandidate, given: Mapping[str, PayloadView], rows: set[str], cfg: SkillConfig
) -> str | None:
    if c.row_key not in rows:
        return "row_not_in_group"
    if c.lane is not None and c.lane not in cfg.lanes:
        return "unknown_lane"
    problem = evidence_problem(c.evidence, given)
    if problem is None and any(given[e.payload_ref].row_key != c.row_key for e in c.evidence):
        return "row_not_in_evidence"
    return problem


def _people(out: PeopleOutput, given: Mapping[str, PayloadView], result: ExtractionResult) -> None:
    for person in out.people:
        problem = evidence_problem(person.evidence, given)
        quotes = [e.quote for e in person.evidence]
        if problem is None and not quoted(person.title, quotes):
            problem = "invented_title"
        if problem is None and person.tenure_text and not quoted(person.tenure_text, quotes):
            problem = "invented_tenure"
        if problem is None and not quoted(person.name, quotes):
            problem = "invented_name"
        if problem:
            result.drops[problem] += 1
        else:
            result.people.append(person)


def groups(
    cfg: SkillConfig, payloads: Sequence[PayloadView], step: Literal["pass1", "pass2", "people"]
) -> list[ExtractionInput]:
    """The step's source groups (route need), each with its payloads, in a stable order."""
    by_need: dict[str, list[PayloadView]] = {}
    for p in sorted(payloads, key=lambda v: (v.row_key, v.payload_ref)):
        row = cfg.plan[p.row_key]
        if row.step == step:
            by_need.setdefault(row.need, []).append(p)
    return [
        ExtractionInput(step=step, group=need, payloads=tuple(views))
        for need, views in sorted(by_need.items())
    ]
