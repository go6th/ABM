"""Interpretation of stored pulls (Phase 2): the Pass 1, Pass 2 and People agents (steps 2 to 4).

One model call per source group (step and route need) reads only that group's stored payloads
(quarantined text) and proposes candidates. Code then decides what survives:

* G4: its evidence must cite payloads this call was given and quote them verbatim;
* its attribute decides its row: an attribute of the vocabulary (``config/skill/attributes.yaml``)
  files the candidate under that attribute's canonical row whatever row the page was pulled for
  (the headquarters found on a podcast page is a baseline fact); an attribute outside the
  vocabulary stays under the row it was pulled for, which must be a row of the group and the row
  of every payload it quotes, and is counted;
* its lane must be a skill lane (or none); a candidate with no lane whose quotes name the
  platforms of exactly one lane gets that lane (code, from the job lexicon);
* G2: a declared keyword match or date range stays one, and a payload code knows is a match makes
  the candidate one whatever the model claims; "read this run" needs full-page reads (the grading
  engine decides Confirmed itself and grades an unread match Lead);
* private payloads make the candidate private (G10);
* repeats of the same (row, attribute, value) in one call merge into one candidate carrying every
  quote (one fact with several sources, not four facts), counted;
* people: the title, and any tenure, must be quoted in the evidence (no invented titles);
* a fact whose attribute is a named person (``executive_role``) must name one: a value that is
  only a title ("Vice President of Technology Product Management") is dropped, counted
  ``person_fact_without_name`` (``agents.people_names.names_a_person``);
* G3: a lead-drop request is honoured only with three phrasings across two tool categories.

Drops are counted by reason; nothing is silently lost.

GUARANTEE(test_fabricated_quotes_and_urls_never_become_facts)
GUARANTEE(test_invented_titles_names_and_tenure_are_dropped)
GUARANTEE(test_promoted_lead_from_a_search_snippet_stays_a_lead)
GUARANTEE(test_vocabulary_attributes_are_refiled_merged_and_laned)
GUARANTEE(test_executive_role_naming_nobody_is_dropped_and_counted)
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from agents.config import SkillConfig
from agents.contracts import AgentContract, BudgetMeter
from agents.errors import OutputRejected
from agents.guards import DropDecision, evidence_problem, lead_drop_decision, quoted
from agents.lanes import infer_lane
from agents.people_names import NAMED_PERSON_ATTRIBUTES, names_a_person
from agents.ports import AgentModel, GradingCandidate, RunContext
from agents.runtime import call_agent
from agents.schemas import (
    AttributeName,
    EvidenceRef,
    ExtractionInput,
    ExtractionOutput,
    FactCandidate,
    PayloadView,
    PeopleOutput,
    PersonCandidate,
)

FULL_READ_NEEDS = frozenset({"page_read", "scrape"})
MAX_EVIDENCE = 5


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


def value_key(value: str) -> str:
    """The folded form two values are the same by (NFKC, whitespace, case, end punctuation)."""
    folded = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    return folded.rstrip(".,;:")


async def extract(
    contract: AgentContract,
    model: AgentModel,
    meter: BudgetMeter,
    ctx: RunContext,
    inp: ExtractionInput,
    cfg: SkillConfig,
    allowed_lanes: Sequence[str] | None = None,
) -> ExtractionResult:
    """Run one extraction call and filter its output (see the module docstring). ``allowed_lanes``
    are the run's lanes for lane inference (the skill's lanes when None)."""
    result = ExtractionResult()
    if not inp.payloads:
        return result
    inp = inp.model_copy(update={"account_name": ctx.account_name, "domain": ctx.domain})
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
    lanes = tuple(allowed_lanes) if allowed_lanes is not None else cfg.lanes
    merged: dict[tuple[str, str, str], GradingCandidate] = {}
    for c in out.candidates:
        problem, row = _place(c, given, rows, cfg)
        if problem:
            result.drops[problem] += 1
            continue
        assert row is not None
        if c.attribute in NAMED_PERSON_ATTRIBUTES and not names_a_person(c.value):
            # a role with nobody named in it is not a person (shown as one until 2026-10-27)
            result.drops["person_fact_without_name"] += 1
            continue
        if row != c.row_key:
            result.drops["refiled"] += 1
        lane = c.lane
        if lane is None:
            lane = infer_lane((c.value, *(e.quote for e in c.evidence)), lanes)
            if lane is not None:
                result.drops["lane_inferred"] += 1
        c = c.model_copy(update={"row_key": row, "lane": lane})
        origin, read = _origin(c, given, cfg)
        evidence_payloads = [given[e.payload_ref] for e in c.evidence]
        private = any(p.visibility == "private" for p in evidence_payloads)
        item = GradingCandidate(
            candidate=c.model_copy(update={"origin": origin}),
            urls=tuple(p.url for p in evidence_payloads),
            visibility="private" if private else "public",
            read_this_run=read,
        )
        key = (row, c.attribute, value_key(c.value))
        if key in merged:
            merged[key] = _merge(merged[key], item)
            result.drops["merged_repeat"] += 1
        else:
            merged[key] = item
    result.candidates.extend(merged.values())
    for drop in out.lead_drops:
        decision = lead_drop_decision(drop.attempts, cfg.min_phrasings, cfg.min_tool_categories)
        result.lead_drops[drop.lead_key] = decision
        if not decision.allowed:
            result.drops[f"lead_kept_{decision.reason}"] += 1
    return result


def _merge(kept: GradingCandidate, more: GradingCandidate) -> GradingCandidate:
    """One candidate carrying both candidates' quotes (at most ``MAX_EVIDENCE``): read this run
    when either was, a read origin when either was read, private when either was, the earlier
    event date kept."""
    seen = {(e.payload_ref, e.quote) for e in kept.candidate.evidence}
    evidence: list[EvidenceRef] = list(kept.candidate.evidence)
    urls: list[str] = list(kept.urls)
    for ref, url in zip(more.candidate.evidence, more.urls, strict=True):
        if (ref.payload_ref, ref.quote) in seen or len(evidence) >= MAX_EVIDENCE:
            continue
        seen.add((ref.payload_ref, ref.quote))
        evidence.append(ref)
        urls.append(url)
    read = kept.read_this_run or more.read_this_run
    origin = kept.candidate.origin
    if more.candidate.origin == "read":
        origin = "read"
    lane = kept.candidate.lane or more.candidate.lane
    date = kept.candidate.event_date or more.candidate.event_date
    private = kept.visibility == "private" or more.visibility == "private"
    return GradingCandidate(
        candidate=kept.candidate.model_copy(
            update={
                "evidence": tuple(evidence),
                "origin": origin,
                "lane": lane,
                "event_date": date,
            }
        ),
        urls=tuple(urls),
        visibility="private" if private else "public",
        read_this_run=read,
    )


def _place(
    c: FactCandidate, given: Mapping[str, PayloadView], rows: set[str], cfg: SkillConfig
) -> tuple[str | None, str | None]:
    """``(problem, row)``: the first reason the candidate is dropped, or the row it files under
    (its attribute's canonical row, else the row it was pulled for)."""
    if c.lane is not None and c.lane not in cfg.lanes:
        return "unknown_lane", None
    problem = evidence_problem(c.evidence, given)
    if problem is not None:
        return problem, None
    spec = cfg.attributes.get(c.attribute)
    if spec is not None:
        return None, spec.row_key
    if c.row_key not in rows:
        return "row_not_in_group", None
    if any(given[e.payload_ref].row_key != c.row_key for e in c.evidence):
        return "row_not_in_evidence", None
    return None, c.row_key


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


# One extraction call reads at most this much payload text (about 30k tokens), far inside the
# model's context: a group with more is split into several calls, and one payload larger than
# this is cut to it (quotes are checked against the text the call was given, so G4 still holds).
MAX_CALL_CHARS = 120_000


def _batches(views: Sequence[PayloadView], limit: int) -> list[tuple[PayloadView, ...]]:
    out: list[tuple[PayloadView, ...]] = []
    batch: list[PayloadView] = []
    size = 0
    for view in views:
        if len(view.text) > limit:
            view = view.model_copy(update={"text": view.text[:limit]})
        if batch and size + len(view.text) > limit:
            out.append(tuple(batch))
            batch, size = [], 0
        batch.append(view)
        size += len(view.text)
    if batch:
        out.append(tuple(batch))
    return out


def vocabulary(cfg: SkillConfig, rows: Sequence[str]) -> tuple[AttributeName, ...]:
    """The attribute names one call may use: those of ``rows`` and the shared names, each with
    its canonical row and meaning, in a stable order."""
    wanted = set(rows)
    names = [
        a
        for a in cfg.attributes.values()
        if a.row_key in wanted or a.attribute in cfg.shared_attributes
    ]
    return tuple(
        AttributeName(attribute=a.attribute, row_key=a.row_key, meaning=a.meaning[:300])
        for a in sorted(names, key=lambda a: (a.row_key, a.attribute))
    )


def groups(
    cfg: SkillConfig,
    payloads: Sequence[PayloadView],
    step: Literal["pass1", "pass2", "people"],
    max_chars: int = MAX_CALL_CHARS,
) -> list[ExtractionInput]:
    """The step's source groups (route need), each with its payloads and the attribute names it
    may use, in a stable order; a group whose text exceeds ``max_chars`` becomes several
    consecutive inputs. Rows a code pipeline owns (postings, detections) are not read here."""
    by_need: dict[str, list[PayloadView]] = {}
    for p in sorted(payloads, key=lambda v: (v.row_key, v.payload_ref)):
        row = cfg.plan[p.row_key]
        if row.step == step and row.pipeline is None:
            by_need.setdefault(row.need, []).append(p)
    out: list[ExtractionInput] = []
    for need, views in sorted(by_need.items()):
        names = vocabulary(cfg, sorted({v.row_key for v in views}))
        for batch in _batches(views, max_chars):
            out.append(ExtractionInput(step=step, group=need, payloads=batch, attributes=names))
    return out
