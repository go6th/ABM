"""Account Summary mapping (pure), in the PRD order (s3.4).

* Authored text (narrative, caveats, hypotheses, first-call questions, what would change a
  decision, executive voice) comes only from stored ``report_sections`` rows written by the
  distill step; a run without a narrative is refused (``missing_narrative``). Sections are
  validated, never edited.
* Lane tables come from the run's current public facts; the statement policy and the display
  label are the grading engine's (B7). Absence claims have a search proof, not a source, so they
  are listed with the hypotheses under their own heading instead of in a lane table.
* Key people are the People List's priority A rows, in the People List's order.
* Findings from LinkedIn are the people pipeline's validated findings for listed people only.
* Every number in authored text must be registered or appear in the rows the block cites (B6);
  a narrative or what-would-change block that cites a fact which may not be stated must be a
  question (D-24 proposal).
* A block that breaks one of those content rules, or the D-24 prose rules, is withheld from the
  file (listed in the manifest as ``withheld_blocks``) and the build goes on; only a missing
  narrative, a provenance gap or a leak fails the build (staging 2026-10-21: one block held back
  every file of a finished research).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from account_intelligence.grading.product import display_label, statement_policy
from exports.content import EvidenceRow, FactRow, SectionRow
from exports.models import (
    AuthoredBlock,
    FindingBlock,
    HypothesisBlock,
    KeyPersonRow,
    LaneRow,
    LaneTable,
    PeopleModel,
    QuoteBlock,
    SummaryModel,
)
from exports.numbers import unregistered
from exports.policy import fact_violations, sanitise, visibility_violations
from exports.readers.common import MapContext, Mapped, digest, ref
from exports.readers.tracker import map_tracker

SECTION_KINDS = (
    "narrative",
    "caveat",
    "executive_voice",
    "hypothesis",
    "first_call_question",
    "decision_change",
)
# Sections whose every block must cite at least one source code.
_CITED_KINDS = ("narrative", "executive_voice", "decision_change")
_GRADE_ORDER = {"confirmed": 0, "probable": 1, "hypothesis": 2, "lead": 3}


def _fact_codes(ctx: MapContext, fact: FactRow, evidence: Sequence[EvidenceRow]) -> tuple[str, ...]:
    codes: set[str] = set()
    for ev in evidence:
        if ev.evidence_kind == "search_proof":
            continue
        code = ctx.code_or_fail(ev.source_code, ev.url, "summary.lanes", fact.id)
        if code:
            codes.add(code)
    return tuple(sorted(codes))


def _label(fact: FactRow, evidence: Sequence[EvidenceRow]) -> str:
    flags = set(fact.flags)
    sourced = [e for e in evidence if e.evidence_kind != "search_proof"]
    if sourced and all(e.is_aggregator for e in sourced):
        flags.add("aggregator")
    return display_label(fact.product_grade, tuple(sorted(flags)), fact.lead_status)  # type: ignore[arg-type]


def _lane_tables(
    ctx: MapContext, by_fact: dict[str, list[EvidenceRow]]
) -> tuple[tuple[LaneTable, ...], tuple[FactRow, ...], dict[str, FactRow]]:
    c = ctx.content
    where = "summary.lanes"
    ctx.flag(visibility_violations(((f.id, f.visibility) for f in c.facts), where))
    public = [f for f in c.facts if f.visibility == "public"]
    absences = tuple(f for f in public if f.claim_kind == "absence")
    graded = [f for f in public if f.claim_kind != "absence"]
    if ctx.capped(len(graded), ctx.rules.limits.row_caps.summary_lane_rows, where):
        return (), absences, {}
    for f in public:
        ctx.flag(fact_violations(f, ctx.grading))
    graded.sort(
        key=lambda f: (
            ctx.cfg.lane_rank(f.lane),
            f.lane or "",
            _GRADE_ORDER.get(f.product_grade, 9),
            f.attribute_key,
            f.id,
        )
    )
    groups: dict[str, list[LaneRow]] = {}
    order: list[str] = []
    rendered: dict[str, FactRow] = {}
    i = 0
    for f in graded:
        evidence = by_fact.get(f.id, [])
        codes = _fact_codes(ctx, f, evidence)
        if not ctx.printable(f.value_text, where, f.id):
            continue
        if not codes:
            ctx.fail("uncited_graded_row", where, f.id)
            continue
        sourced = [e for e in evidence if e.evidence_kind != "search_proof"]
        if sourced and all(e.is_aggregator for e in sourced) and f.product_grade != "lead":
            ctx.fail("aggregator_not_lead", where, f.id)
        if f.product_grade not in _GRADE_ORDER:
            continue  # refused by fact_violations
        may_state = statement_policy(f.product_grade, ctx.grading)  # type: ignore[arg-type]
        lane = ctx.cfg.lane_label(f.lane) or ctx.rules.headings.summary.unassigned_lane
        i += 1
        row = LaneRow(
            ref=ref("L", i),
            row_id=f.id,
            finding=ctx.rendered(f.value_text, where),
            grade_label=_label(f, evidence),
            use=ctx.rules.statement_text.may_state if may_state else ctx.rules.statement_text.ask,
            may_state=may_state,
            question=ctx.rendered(f.confirming_question, where) if f.confirming_question else None,
            codes=codes,
        )
        if lane not in groups:
            groups[lane] = []
            order.append(lane)
        groups[lane].append(row)
        rendered[f.id] = f
    return tuple(LaneTable(lane=k, rows=tuple(groups[k])) for k in order), absences, rendered


def _cited_texts(
    ctx: MapContext, s: SectionRow, by_fact: dict[str, list[EvidenceRow]]
) -> list[str]:
    facts = {f.id: f for f in ctx.content.facts if f.visibility == "public"}
    texts: list[str] = []
    for fid in s.fact_ids:
        f = facts.get(fid)
        if f:
            texts.append(f.value_text)
            texts.extend(e.supporting_sentence for e in by_fact.get(fid, []))
    codes = set(s.codes)
    for f in facts.values():
        for e in by_fact.get(f.id, []):
            code = e.source_code or ctx.index.code_for(e.url)
            if code in codes:
                texts.append(e.supporting_sentence)
                texts.append(f.value_text)
    return texts


def _sections(
    ctx: MapContext, by_fact: dict[str, list[EvidenceRow]], rendered: dict[str, FactRow]
) -> dict[str, list[SectionRow]]:
    where = "summary.sections"
    c = ctx.content
    ctx.flag(visibility_violations(((s.id, s.visibility) for s in c.sections), where))
    if ctx.capped(len(c.sections), ctx.rules.limits.row_caps.sections, where):
        return {}
    out: dict[str, list[SectionRow]] = defaultdict(list)
    allowed = ctx.registry.allowed()
    limit = ctx.rules.limits.authored_text_chars
    for s in sorted(c.sections, key=lambda s: (s.kind, s.position, s.id)):
        w = f"{where}.{s.kind}.{s.id}"
        if s.visibility != "public":
            continue
        if s.kind not in SECTION_KINDS:
            ctx.fail("section_kind_vocabulary", where, s.id)
            continue
        if len(s.text) > limit:
            ctx.fail("section_text_length", where, s.id)
            continue
        if not ctx.printable(s.text, where, s.id):
            continue
        if s.kind in _CITED_KINDS and not s.codes:
            ctx.fail("narrative_without_code", where, s.id)
        for code in s.codes:
            if not ctx.index.knows(code):
                ctx.fail("unknown_source_code", where, code)
        # Content rules on the block's own prose withhold the block (listed in the manifest,
        # counted) and the build goes on: the research stands, one sentence does not hold
        # every file back. Provenance and leaks above still fail the build.
        rule = _withhold_rule(ctx, s, rendered, allowed, by_fact)
        if rule is not None:
            ctx.withhold(w, s.id, rule)
            continue
        out[s.kind].append(s)
    return out


def _withhold_rule(
    ctx: MapContext,
    s: SectionRow,
    rendered: dict[str, FactRow],
    allowed: frozenset[str],
    by_fact: dict[str, list[EvidenceRow]],
) -> str | None:
    """The first content rule that keeps a section out of the Summary, or None. A hypothesis
    needs its question and an executive quote its speaker; a block may cite only facts the lane
    tables show; a statement may rest only on facts the policy lets a client file state; a
    number must be registered or in the cited rows (B6); the D-24 prose rules apply to every
    authored block (an executive quote is verbatim and exempt)."""
    if s.kind == "hypothesis" and not sanitise(s.confirming_question or "").strip():
        return "question_missing"
    if s.kind == "executive_voice" and not sanitise(s.speaker or "").strip():
        return "speaker_missing"
    if any(fid not in rendered for fid in s.fact_ids):
        return "section_fact_not_rendered"
    states = s.kind in ("narrative", "decision_change") and not s.text.rstrip().endswith("?")
    if states and any(
        fid in rendered and not statement_policy(rendered[fid].product_grade, ctx.grading)  # type: ignore[arg-type]
        for fid in s.fact_ids
    ):
        return "unconfirmed_stated"
    if s.kind == "executive_voice":
        return None
    if unregistered(s.text, allowed, _cited_texts(ctx, s, by_fact)):
        return "unregistered_number"
    where = f"summary.{s.kind}.{s.id}"
    prose = ctx.prose_violations(s.text, where)
    if s.kind == "hypothesis":
        prose += ctx.prose_violations(s.confirming_question or "", where)
    return prose[0].code if prose else None


def _block(ctx: MapContext, s: SectionRow) -> AuthoredBlock:
    # the prose rules were applied when the section was selected (``_withhold_rule``)
    return AuthoredBlock(
        row_id=s.id,
        text=ctx.rendered(s.text, f"summary.{s.kind}.{s.id}"),
        codes=tuple(c for c in s.codes if ctx.index.knows(c)),
    )


def _why_no_narrative(facts: Sequence[FactRow], evidence: Sequence[EvidenceRow]) -> str:
    """A readable reason for a missing narrative: the narrative may only state Confirmed facts,
    and only a full page read of a primary source this run can make a fact Confirmed (G5)."""
    grades = Counter(f.product_grade for f in facts)
    confirmed = grades.get("confirmed", 0)
    full = {e.url for e in evidence if e.origin == "full_page_read" and e.url}
    origins = Counter(e.origin or "unknown" for e in evidence)
    by_grade = ", ".join(f"{g} {n}" for g, n in sorted(grades.items())) or "none"
    by_origin = ", ".join(f"{o} {n}" for o, n in sorted(origins.items())) or "none"
    why = (
        "no facts were graded"
        if not facts
        else "no fact was read in full in a primary source this run"
        if confirmed == 0
        else "distill wrote no narrative section"
    )
    return (
        f"{confirmed} Confirmed facts of {len(facts)} ({by_grade}); "
        f"{len(full)} pages cited from full reads; evidence by origin: {by_origin}; reason: {why}"
    )


def map_summary(ctx: MapContext, people: PeopleModel | None) -> Mapped[SummaryModel]:
    """The Summary render model, or ``Mapped(None)`` when a required part is missing."""
    c = ctx.content
    by_fact: dict[str, list[EvidenceRow]] = defaultdict(list)
    for ev in sorted(c.evidence, key=lambda e: (e.fact_id, e.id)):
        by_fact[ev.fact_id].append(ev)
    lanes, absence_facts, rendered = _lane_tables(ctx, by_fact)
    tracker, tally = map_tracker(ctx)
    sections = _sections(ctx, by_fact, rendered)
    if not sections.get("narrative"):
        ctx.fail("missing_narrative", "summary", _why_no_narrative(c.facts, c.evidence))
        return Mapped(None)

    key_people: list[KeyPersonRow] = []
    if people is not None:
        for p in people.rows:
            if p.priority == "A":
                key_people.append(
                    KeyPersonRow(
                        ref=ref("K", len(key_people) + 1),
                        row_id=p.row_id,
                        name=p.name,
                        title=p.title,
                        level=p.level,
                        lanes=p.lanes,
                        priority=p.priority,
                        profile_url=p.profile_url,
                        source_code=p.source_code,
                    )
                )
    listed = {p.row_id: p.name for p in people.rows} if people is not None else {}
    ctx.registry.add(
        "summary.key_people",
        len(key_people),
        "priority A people on the People List",
        f"{len(key_people)} priority A people",
    )

    findings: list[FindingBlock] = []
    for f in c.findings:
        if f.person_id not in listed:
            continue  # a person not on the list never appears (B9)
        where = "summary.linkedin_findings"
        code = ctx.code_or_fail(None, f.url, where, f.person_id)
        row = f"{f.person_id}:{f.url}"
        if not (ctx.printable(f.claim, where, row) and ctx.printable(f.sentence, where, row)):
            continue
        if code is None:
            continue
        prose = ctx.prose_violations(f.claim, where)
        if prose:
            ctx.withhold(where, row, prose[0].code)  # the templated claim is authored prose
            continue
        sentence, withheld = ctx.quote(f.sentence, where)
        findings.append(
            FindingBlock(
                ref=ref("F", len(findings) + 1),
                row_id=f"{f.person_id}:{f.url}",
                person=listed[f.person_id],
                claim=ctx.rendered(f.claim, where),
                sentence=sentence,
                withheld=withheld,
                url=f.url,
                source_code=code,
            )
        )

    voice: list[QuoteBlock] = []
    for s in sections.get("executive_voice", []):
        text, _ = ctx.quote(s.text, f"summary.executive_voice.{s.id}")
        codes = tuple(x for x in s.codes if ctx.index.knows(x))
        if codes and s.speaker:
            voice.append(
                QuoteBlock(
                    row_id=s.id,
                    text=text,
                    speaker=ctx.rendered(s.speaker, "summary.executive_voice"),
                    codes=codes,
                )
            )

    hypotheses = tuple(
        HypothesisBlock(
            row_id=s.id,
            text=ctx.rendered(s.text, f"summary.hypothesis.{s.id}"),
            question=ctx.rendered(s.confirming_question or "", f"summary.hypothesis.{s.id}"),
            codes=tuple(x for x in s.codes if ctx.index.knows(x)),
        )
        for s in sections.get("hypothesis", [])
    )
    absences = tuple(
        HypothesisBlock(
            row_id=f.id,
            text=ctx.rendered(f.value_text, "summary.absence"),
            question=ctx.rendered(f.confirming_question, "summary.absence"),
            codes=(),
        )
        for f in sorted(absence_facts, key=lambda f: (f.attribute_key, f.id))
        if ctx.printable(f.value_text, "summary.absence", f.id)
        and ctx.printable(f.confirming_question, "summary.absence", f.id)
    )
    if not tracker:
        return Mapped(None)
    model = SummaryModel(
        brand=ctx.rendered(c.brand_name, "branding") if c.brand_name else None,
        account=c.account_name,
        as_of=c.as_of,
        narrative=tuple(_block(ctx, s) for s in sections["narrative"]),
        caveats=tuple(_block(ctx, s) for s in sections.get("caveat", [])),
        sensitive_sentence=ctx.rules.sensitive_sentence.text,
        lanes=lanes,
        key_people=tuple(key_people),
        linkedin_findings=tuple(findings),
        executive_voice=tuple(voice),
        hypotheses=hypotheses,
        absences=absences,
        first_call_questions=tuple(_block(ctx, s) for s in sections.get("first_call_question", [])),
        decision_changes=tuple(_block(ctx, s) for s in sections.get("decision_change", [])),
        tracker=tracker,
        tracker_tally=tally,
        none_found=ctx.rules.none_found.text,
    )
    lane_rows = [r for t in lanes for r in t.rows]
    keyed = [
        *((r.row_id, r.ref, digest(r)) for r in lane_rows),
        *((r.row_id, r.ref, digest(r)) for r in key_people),
        *((r.row_id, r.ref, digest(r)) for r in findings),
        *((r.row_id, r.ref, digest(r)) for r in tracker),
    ]
    blocks: list[AuthoredBlock] = [
        *model.narrative,
        *model.caveats,
        *model.first_call_questions,
        *model.decision_changes,
    ]
    texts: list[AuthoredBlock | QuoteBlock | HypothesisBlock] = [*blocks, *voice, *hypotheses]
    keyed += [(b.row_id, "", digest(b)) for b in (*texts, *absences)]
    cited = {code for r in lane_rows for code in r.codes}
    cited |= {p.source_code for p in key_people} | {f.source_code for f in findings}
    cited |= {code for b in texts for code in b.codes}
    return Mapped(
        model,
        row_ids=tuple(k[0] for k in keyed),
        digests=tuple(k[2] for k in keyed),
        refs=tuple(k[1] for k in keyed if k[1]),
        cited=frozenset(cited),
    )
