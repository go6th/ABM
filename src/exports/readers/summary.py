"""Account Summary mapping (pure), in the owner's section order (master prompt v2, 2026-10-24,
section 9.1).

* Authored text (narrative, caveats, hypotheses, first-call questions, what would change a
  decision, executive voice) comes only from stored ``report_sections`` rows written by the
  distill step; a run without a narrative is refused (``missing_narrative``). Sections are
  validated, never edited.
* Lane tables come from the run's current public facts; the statement policy and the display
  label are the grading engine's (B7). Absence claims have a search proof, not a source, so they
  are listed with the hypotheses under their own heading instead of in a lane table.
* Recent news, the baseline and the signal tables are views of the same lane rows, sorted by the
  attribute's tier (``config/skill/attributes.yaml``) and the fact's event date against the news
  window (``config/skill/scope.yaml``): a row in those tables is always a graded lane row with
  the same sources, so nothing is cited twice differently.
* Key people are the People List's priority A rows and its executives (the most senior level),
  in the People List's order; people whose status changed are listed people whose pulled
  profile shows another current title than the title search returned.
* The Platform picture by lane is one table of the lane rows (Item from the lane and the
  attribute vocabulary, the statement policy column kept, reasoning and sources in one cell).
* Findings from profiles are the people pipeline's validated findings for listed people only.
* Every number in authored text must be registered or appear in the rows the block cites (B6);
  a narrative or what-would-change block that cites a fact which may not be stated must be a
  question (D-24 proposal).
* A block that breaks one of those content rules, or the D-24 prose rules, is withheld from the
  file (listed in the manifest as ``withheld_blocks``) and the build goes on; only a missing
  narrative, a provenance gap or a leak fails the build (staging 2026-10-21: one block held back
  every file of a finished research).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date

from account_intelligence.grading.product import display_label, statement_policy
from account_intelligence.tracker.render import STATUS_TEXT
from exports.content import EvidenceRow, FactRow, SectionRow
from exports.models import (
    AuthoredBlock,
    BaselineRow,
    FindingBlock,
    GradeLegendRow,
    HypothesisBlock,
    JobsModel,
    KeyPersonRow,
    LaneMeaningBlock,
    LaneRow,
    LaneTable,
    NewsRow,
    PeopleModel,
    PlatformHiringRow,
    QuoteBlock,
    SignalRow,
    SignalTable,
    SourceLinkRow,
    StatusChangeRow,
    SummaryModel,
    TrackerRow,
    WaitingRow,
)
from exports.numbers import unregistered
from exports.policy import fact_violations, sanitise, visibility_violations
from exports.readers.common import (
    MapContext,
    Mapped,
    digest,
    host_of,
    months_before,
    parse_event_date,
    ref,
)
from exports.readers.tracker import SIGNAL_TIERS, map_tracker, tally_line
from exports.workspace import account_slug

SECTION_KINDS = (
    "narrative",
    "caveat",
    "executive_voice",
    "hypothesis",
    "first_call_question",
    "decision_change",
    "stands_out",
    "lane_meaning",
)
# Sections whose every block must cite at least one source code.
_CITED_KINDS = ("narrative", "executive_voice", "decision_change", "stands_out", "lane_meaning")
# Sections whose sentences state facts: only facts a client file may state, or a question.
_STATING_KINDS = ("narrative", "decision_change", "stands_out", "lane_meaning")
_GRADE_ORDER = {"confirmed": 0, "probable": 1, "hypothesis": 2, "lead": 3}
# the grades a client file explains (``internal`` never reaches a file, B8)
_LEGEND_GRADES = ("confirmed", "probable", "hypothesis", "lead")
_SIGNAL_KEYS = {1: "tier1", 2: "tier2", 3: "tier3"}
_OTHER_FILES = (("evidence", "docx"), ("people", "xlsx"), ("jobs", "xlsx"), ("tech", "xlsx"))


def _fact_codes(ctx: MapContext, fact: FactRow, evidence: Sequence[EvidenceRow]) -> tuple[str, ...]:
    codes: set[str] = set()
    for ev in evidence:
        if ev.evidence_kind == "search_proof" or ctx.denied(ev.source_code, ev.url):
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
    listed: set[tuple[str, str, tuple[str, ...], str]] = set()
    i = 0
    for f in graded:
        evidence = by_fact.get(f.id, [])
        codes = _fact_codes(ctx, f, evidence)
        if not ctx.printable(f.value_text, where, f.id):
            continue
        if not codes and any(ctx.denied(e.source_code, e.url) for e in evidence):
            # every source of the finding links a data tool: the row is withheld and listed
            ctx.withhold(where, f.id, "tool_link")
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
        finding = ctx.rendered(f.value_text, where)
        # the same finding from the same sources twice is one row (a repeat graded twice
        # before the extraction merged repeats, 2026-10-24: Tolmar's Summary listed every
        # finding twice); the second fact still counts as rendered for the sections' sake
        key = (lane, finding.casefold(), codes, _label(f, evidence))
        if key in listed:
            rendered[f.id] = f
            continue
        listed.add(key)
        i += 1
        # a Confirmed finding may be stated: it carries no confirming question (the skill: only
        # what is below Confirmed becomes a question)
        question = (
            ctx.rendered(f.confirming_question, where)
            if f.confirming_question and not may_state
            else None
        )
        read = _read_label(ctx, f, evidence)
        row = LaneRow(
            ref=ref("L", i),
            row_id=f.id,
            finding=finding,
            grade_label=_label(f, evidence),
            use=ctx.rules.statement_text.may_state if may_state else ctx.rules.statement_text.ask,
            may_state=may_state,
            question=question,
            codes=codes,
            item=ctx.rendered(_lane_item(ctx, f), where),
            read=read,
            reasoning=_reasoning(ctx, read, codes, question),
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
    if s.kind == "lane_meaning" and ctx.cfg.lane_label(s.lane) is None:
        return "lane_unknown"
    states = s.kind in _STATING_KINDS and not s.text.rstrip().endswith("?")
    if states and any(
        fid in rendered and not statement_policy(rendered[fid].product_grade, ctx.grading)  # type: ignore[arg-type]
        for fid in s.fact_ids
    ):
        return "unconfirmed_stated"
    if ctx.leaks(s.text) or (s.kind == "hypothesis" and ctx.leaks(s.confirming_question or "")):
        return "leak"
    if s.codes and all(code in ctx.denied_codes for code in s.codes):
        return "tool_link"
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
        codes=tuple(c for c in s.codes if ctx.index.knows(c) and c not in ctx.denied_codes),
    )


def _lane_block(ctx: MapContext, s: SectionRow) -> LaneMeaningBlock:
    """A lane meaning with its lane's client label (an unknown lane was withheld on selection)."""
    b = _block(ctx, s)
    lane = ctx.rendered(ctx.cfg.lane_label(s.lane), f"summary.{s.kind}.{s.id}")
    return LaneMeaningBlock(row_id=b.row_id, text=b.text, codes=b.codes, lane=lane)


def _why_no_narrative(
    facts: Sequence[FactRow], evidence: Sequence[EvidenceRow], withheld_rules: Sequence[str] = ()
) -> str:
    """A readable reason for a missing narrative: the narrative may only state Confirmed facts,
    and only a full page read of a primary source this run can make a fact Confirmed (G5); or
    every narrative block distill wrote was withheld by a content rule (named)."""
    grades = Counter(f.product_grade for f in facts)
    confirmed = grades.get("confirmed", 0)
    full = {e.url for e in evidence if e.origin == "full_page_read" and e.url}
    origins = Counter(e.origin or "unknown" for e in evidence)
    by_grade = ", ".join(f"{g} {n}" for g, n in sorted(grades.items())) or "none"
    by_origin = ", ".join(f"{o} {n}" for o, n in sorted(origins.items())) or "none"
    why = (
        f"every narrative block was withheld ({', '.join(sorted(set(withheld_rules)))})"
        if withheld_rules
        else "no facts were graded"
        if not facts
        else "no fact was read in full in a primary source this run"
        if confirmed == 0
        else "distill wrote no narrative section"
    )
    return (
        f"{confirmed} Confirmed facts of {len(facts)} ({by_grade}); "
        f"{len(full)} pages cited from full reads; evidence by origin: {by_origin}; reason: {why}"
    )


# ---- the tiered views of the lane rows (news, baseline, signals) ---------------------------


def _grade_legend(ctx: MapContext) -> tuple[GradeLegendRow, ...]:
    """How to read this file: the product grades in the skill's wording (``labels.grade_legend``,
    output-spec.md "Grades"), falling back to ``config/grading.yaml``'s meaning for a grade the
    rules do not word. The grading policy decides whether a grade may be stated: a grade it
    forbids never reads "May be stated", and a stateable grade always does."""
    text = ctx.rules.statement_text
    worded = ctx.rules.documents.labels.grade_legend
    rows = []
    for grade in _LEGEND_GRADES:
        own = worded.get(grade)
        meaning = own.meaning if own else ctx.grading.meanings.get(grade)  # type: ignore[call-overload]
        if not meaning:
            continue
        may_state = statement_policy(grade, ctx.grading)  # type: ignore[arg-type]
        if may_state:
            use = text.may_state
        else:
            use = own.use if own and own.use != text.may_state else text.ask
        rows.append(
            GradeLegendRow(
                grade=display_label(grade),  # type: ignore[arg-type]
                meaning=ctx.rendered(meaning, "summary.legend"),
                use=use,
            )
        )
    return tuple(rows)


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _date_text(raw: str | None, when: date) -> str:
    """A finding's date as the reference Summary prints it (``6 Oct 2026``), at the precision
    it was stored with: a month alone prints ``Feb 2026`` and a year alone ``2026``, never a
    first-of-month day the source did not give."""
    parts = (raw or "").strip()[:10].split("-")
    month = _MONTHS[when.month - 1]
    if len(parts) > 2 and parts[2]:
        return f"{when.day} {month} {when.year}"
    if len(parts) > 1 and parts[1]:
        return f"{month} {when.year}"
    return str(when.year)


def _read_label(ctx: MapContext, fact: FactRow, evidence: Sequence[EvidenceRow]) -> str:
    """How the finding was read: in full this run, a headline, a cached copy or a feed item."""
    labels = ctx.rules.documents.labels
    origins = {e.origin for e in evidence if e.evidence_kind != "search_proof"}
    if fact.read_in_primary_this_run or "full_page_read" in origins:
        return str(labels.read_full)
    if "search_snippet" in origins:
        return str(labels.read_headline)
    if "cached_copy" in origins:
        return str(labels.read_cached)
    if origins & {"feed_item", "broker_record"}:
        return str(labels.read_feed)
    return str(labels.read_not)


def _item_label(attribute: str) -> str:
    """The baseline item from the attribute name (``employee_count`` to ``Employee count``)."""
    return attribute.replace("_", " ").strip().capitalize()


def _lane_item(ctx: MapContext, fact: FactRow) -> str:
    """The Platform picture's Item cell: the lane and the attribute the fact answers, from the
    skill vocabulary (``Salesforce (including MuleSoft): Incumbent partner``). A key outside the
    vocabulary is named by its own last part; a fact without a lane is named by the attribute."""
    spec = ctx.attribute(fact.attribute_key)
    name = spec.attribute if spec is not None else fact.attribute_key.rsplit(".", 1)[-1]
    label = _item_label(name)
    lane = ctx.cfg.lane_label(fact.lane)
    return f"{lane}: {label}" if lane else label


def _reasoning(ctx: MapContext, read: str, codes: Sequence[str], question: str | None) -> str:
    """The Platform picture's "Reasoning and sources" cell: how the finding was read, the source
    codes, and the confirming question when the finding may not be stated (``Read in full;
    S-0001, S-0003. Confirming question: ...``)."""
    text = f"{read}; {', '.join(codes)}" if read else ", ".join(codes)
    if question:
        text = f"{text}. {ctx.rules.documents.labels.question_prefix}: {question}"
    return text


def _tiered(
    ctx: MapContext,
    lanes: Sequence[LaneTable],
    rendered: dict[str, FactRow],
    by_fact: dict[str, list[EvidenceRow]],
) -> tuple[tuple[NewsRow, ...], tuple[BaselineRow, ...], tuple[SignalTable, ...], int]:
    """Recent news (dated signals inside the news window, newest first), the baseline (tier 0)
    and the four signal tables (tiers 1 to 3; context and history for signals dated before the
    window), all views of the lane rows. Returns the news window in months as well."""
    labels = ctx.rules.documents.labels
    headings = ctx.rules.headings.summary
    where = "summary.tiers"
    months = ctx.skill.news_window_months
    cutoff = months_before(ctx.content.as_of, months)
    news: list[tuple[date, LaneRow, FactRow, int]] = []
    baseline: list[BaselineRow] = []
    buckets: dict[str, list[tuple[LaneRow, FactRow, int, date | None]]] = {
        "tier1": [],
        "tier2": [],
        "tier3": [],
        "context": [],
    }
    lane_of: dict[str, str] = {}
    for table in lanes:
        for row in table.rows:
            lane_of[row.row_id] = table.lane
            fact = rendered[row.row_id]
            spec = ctx.attribute(fact.attribute_key)
            if spec is None:
                continue
            when = parse_event_date(fact.event_date)
            if spec.tier == 0:
                baseline.append(
                    BaselineRow(
                        row_id=f"baseline:{fact.id}",
                        item=ctx.rendered(_item_label(spec.attribute), where),
                        what=row.finding,
                        grade_label=row.grade_label,
                        may_state=row.may_state,
                        codes=row.codes,
                    )
                )
                continue
            if spec.tier not in SIGNAL_TIERS:
                continue
            if when is not None and when < cutoff:
                buckets["context"].append((row, fact, spec.tier, when))
            else:
                buckets[_SIGNAL_KEYS[spec.tier]].append((row, fact, spec.tier, when))
            if when is not None and when >= cutoff:
                news.append((when, row, fact, spec.tier))
    titles = {
        "tier1": headings.signals_tier1,
        "tier2": headings.signals_tier2,
        "tier3": headings.signals_tier3,
        "context": headings.signals_context,
    }
    news.sort(key=lambda it: (it[0], it[1].ref), reverse=True)
    # Why it matters: the signal tier the finding sits in and its lane (the reference writes
    # "Tier 1: new owner of IT"); the platform stores no authored reason per finding, so the
    # column says only what the classification shows
    news_rows = tuple(
        NewsRow(
            ref=ref("RN", i),
            row_id=f"news:{fact.id}",
            date=_date_text(fact.event_date, when),
            what=row.finding,
            read=_read_label(ctx, fact, by_fact.get(fact.id, [])),
            codes=row.codes,
            why=ctx.rendered(f"{titles[_SIGNAL_KEYS[tier]]} ({lane_of[row.row_id]})", where),
        )
        for i, (when, row, fact, tier) in enumerate(news, start=1)
    )
    tables: list[SignalTable] = []
    n = 0
    for key, items in buckets.items():
        rows: list[SignalRow] = []
        for row, fact, _tier, when in items:
            n += 1
            rows.append(
                SignalRow(
                    ref=ref("SG", n),
                    row_id=f"signal:{fact.id}",
                    finding=row.finding,
                    grade_label=row.grade_label,
                    lanes=lane_of[row.row_id],
                    date=_date_text(fact.event_date, when) if when else labels.not_dated,
                    codes=row.codes,
                )
            )
        tables.append(SignalTable(key=key, title=titles[key], rows=tuple(rows)))
    return news_rows, tuple(baseline), tuple(tables), months


def _waiting(rows: Sequence[TrackerRow]) -> tuple[WaitingRow, ...]:
    """Decisions waiting on the account owner: the Blocked and Not done rows, in table order."""
    open_words = (STATUS_TEXT["blocked"], STATUS_TEXT["not_done"])
    out: list[WaitingRow] = []
    for t in rows:
        if t.status in open_words:
            out.append(
                WaitingRow(
                    ref=ref("W", len(out) + 1),
                    row_id=f"waiting:{t.row_id}",
                    row=t.row,
                    needs=t.needs,
                    status=t.status,
                )
            )
    return tuple(out)


def _source_links(ctx: MapContext, cited: set[str]) -> tuple[SourceLinkRow, ...]:
    """Every source this file cites, once, in code order, with its link and domain."""
    codes = sorted((c for c in cited if ctx.index.knows(c)), key=lambda c: int(c.split("-")[1]))
    return tuple(
        SourceLinkRow(
            ref=ref("SL", i),
            code=code,
            url=ctx.index.by_code[code].url,
            domain=ctx.rendered(host_of(ctx.index.by_code[code].url), "summary.source_links"),
        )
        for i, code in enumerate(codes, start=1)
    )


def _file_names(ctx: MapContext) -> tuple[str, ...]:
    """The other four files of the set, by their delivered names (``output_spec.yaml``)."""
    slug = account_slug(ctx.content.account_name)
    return tuple(
        ctx.cfg.filenames[kind].replace("{account}", slug) + f".{ext}"
        for kind, ext in _OTHER_FILES
        if kind in ctx.cfg.filenames
    )


def _files_line(ctx: MapContext) -> str | None:
    """The opening paragraph's sentence naming the other four files (the reference Summary's
    opening); None when a file name is not configured."""
    slug = account_slug(ctx.content.account_name)
    names = {
        kind: ctx.cfg.filenames[kind].replace("{account}", slug) + f".{ext}"
        for kind, ext in _OTHER_FILES
        if kind in ctx.cfg.filenames
    }
    if len(names) != len(_OTHER_FILES):
        return None
    return str(ctx.rules.documents.labels.files_line.format(**names))


def _document_name(ctx: MapContext) -> str | None:
    """The Summary's own name without the account ("Account Summary"), from its file name."""
    pattern = ctx.cfg.filenames.get("summary")
    if not pattern:
        return None
    return pattern.replace("{account}", "").strip("_ ").replace("_", " ") or None


def _legal_name(ctx: MapContext) -> str | None:
    """The account's legal name from the stored entity lock, when it says more than the account
    name (the reference title reads "BD (Becton, Dickinson and Company)")."""
    lock = ctx.content.entity_lock or {}
    legal = str(lock.get("legal_name") or "").strip()
    if not legal or legal.casefold() == ctx.content.account_name.strip().casefold():
        return None
    return ctx.rendered(legal, "summary.title")


def _platform_hiring(ctx: MapContext, jobs: JobsModel | None) -> tuple[PlatformHiringRow, ...]:
    """The Summary's platform hiring table (the reference Summary's section 5): the Job Postings
    workbook's platform by class counts and the open postings naming each platform, one row per
    platform named at least once. The counts are the workbook's own (registered there)."""
    if jobs is None or not jobs.class_labels:
        return ()
    labels = ctx.rules.labels
    index = {label: i for i, label in enumerate(jobs.class_labels)}

    def by(m_counts: tuple[int, ...], key: str) -> int:
        i = index.get(labels.posting_class.get(key, ""))
        return m_counts[i] if i is not None else 0

    open_word = labels.posting_status["open"]
    all_rows = ctx.rules.workbooks.jobs.matrix_all_rows
    out: list[PlatformHiringRow] = []
    for m in jobs.matrix:
        if m.platform == all_rows or m.rows == 0:
            continue
        named = [r for r in jobs.rows if m.platform.casefold() in r.platforms.casefold()]
        out.append(
            PlatformHiringRow(
                ref=ref("PH", len(out) + 1),
                row_id=f"hiring:{m.platform}",
                platform=m.platform,
                postings=m.rows,
                open_now=sum(1 for r in named if r.status == open_word),
                build=by(m.by_class, "build"),
                admin_owner=by(m.by_class, "admin_owner"),
                working_tool=by(m.by_class, "working_tool"),
                user=by(m.by_class, "user"),
            )
        )
    return tuple(out)


def _key_people(ctx: MapContext, people: PeopleModel | None) -> tuple[KeyPersonRow, ...]:
    """Key people (the reference Summary's section 6): the People List's priority A rows and the
    executives (the first, most senior level label of the People List), in the list's order,
    with the list's lane, priority and note."""
    if people is None:
        return ()
    top = ctx.rules.labels.people_level_order[0]
    out: list[KeyPersonRow] = []
    for p in people.rows:
        if p.priority != "A" and p.level != top:
            continue
        out.append(
            KeyPersonRow(
                ref=ref("K", len(out) + 1),
                row_id=p.row_id,
                name=p.name,
                title=p.title,
                level=p.level,
                lanes=p.lanes,
                priority=p.priority,
                profile_url=p.profile_url,
                source_code=p.source_code,
                note=p.note,
            )
        )
    return tuple(out)


def _title_key(title: str) -> str:
    """A title compared without case, punctuation or spacing (``VP, IT`` equals ``VP IT``)."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", sanitise(title).casefold()).split())


def _status_changes(ctx: MapContext, people: PeopleModel | None) -> tuple[StatusChangeRow, ...]:
    """People whose status changed: a listed person whose pulled profile shows a current title
    other than the title the search returned (Was the search title, Now the profile's), cited
    to the profile. A profile title the leak scan fires on is left out, never printed."""
    if people is None:
        return ()
    where = "summary.status_changed"
    stored = {p.id: p for p in ctx.content.people}
    out: list[StatusChangeRow] = []
    for row in people.rows:
        person = stored.get(row.row_id)
        now = (person.profile_title or "") if person is not None else ""
        if not sanitise(now).strip() or not row.title.strip() or ctx.leaks(now):
            continue
        if _title_key(now) == _title_key(person.title if person else row.title):
            continue
        out.append(
            StatusChangeRow(
                ref=ref("SC", len(out) + 1),
                row_id=f"status:{row.row_id}",
                person=row.name,
                search_title=row.title,
                profile_title=ctx.rendered(now, where),
                source_code=row.source_code,
            )
        )
    return tuple(out)


def map_summary(
    ctx: MapContext, people: PeopleModel | None, jobs: JobsModel | None = None
) -> Mapped[SummaryModel]:
    """The Summary render model, or ``Mapped(None)`` when a required part is missing."""
    c = ctx.content
    labels = ctx.rules.documents.labels
    by_fact: dict[str, list[EvidenceRow]] = defaultdict(list)
    for ev in sorted(c.evidence, key=lambda e: (e.fact_id, e.id)):
        by_fact[ev.fact_id].append(ev)
    lanes, absence_facts, rendered = _lane_tables(ctx, by_fact)
    tracker, tally = map_tracker(ctx)
    sections = _sections(ctx, by_fact, rendered)
    if not sections.get("narrative"):
        held = [rule for w, _, rule in ctx.withheld_blocks if ".narrative." in w]
        ctx.fail("missing_narrative", "summary", _why_no_narrative(c.facts, c.evidence, held))
        return Mapped(None)

    key_people = _key_people(ctx, people)
    status_changes = _status_changes(ctx, people)
    listed = {p.row_id: p.name for p in people.rows} if people is not None else {}
    ctx.registry.add(
        "summary.key_people",
        len(key_people),
        "priority A people and executives on the People List",
        f"{len(key_people)} priority A people and executives",
    )
    people_intro: tuple[str, ...] = ()
    if people is not None:
        # the Summary sheet's lines as sentences (counts registered by the People mapper); a
        # run without people counts has only the explicit "None found" line, printed as such
        counted = [line for line in people.summary if line.status != ctx.rules.none_found.text]
        people_intro = (
            *people.sentences,
            *(f"{line.count} {line.status} {line.reason}".strip() for line in counted),
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
    news, baseline, signals, months = _tiered(ctx, lanes, rendered, by_fact)
    news_sentence = labels.news_sentence.format(n=len(news), months=months)
    ctx.registry.add(
        "summary.news",
        len(news),
        f"dated findings from the last {months} months",
        news_sentence,
    )
    ctx.registry.add_sentence("summary.news_sentence", news_sentence)
    waiting = _waiting(tracker)
    ctx.registry.add(
        "summary.waiting",
        len(waiting),
        "coverage rows waiting on the account owner",
        f"{len(waiting)} coverage rows wait on the account owner",
    )
    lane_rows = [r for t in lanes for r in t.rows]
    stands_out = tuple(_block(ctx, s) for s in sections.get("stands_out", []))
    lane_meanings = tuple(_lane_block(ctx, s) for s in sections.get("lane_meaning", []))
    blocks: list[AuthoredBlock] = [
        *(_block(ctx, s) for s in sections["narrative"]),
        *stands_out,
        *lane_meanings,
        *(_block(ctx, s) for s in sections.get("caveat", [])),
        *(_block(ctx, s) for s in sections.get("first_call_question", [])),
        *(_block(ctx, s) for s in sections.get("decision_change", [])),
    ]
    texts: list[AuthoredBlock | QuoteBlock | HypothesisBlock] = [*blocks, *voice, *hypotheses]
    cited = {code for r in lane_rows for code in r.codes}
    cited |= {p.source_code for p in key_people} | {f.source_code for f in findings}
    cited |= {s.source_code for s in status_changes}
    cited |= {code for b in texts for code in b.codes}
    links = _source_links(ctx, cited)
    hiring = _platform_hiring(ctx, jobs)
    version = f"{c.run_id[:8]} ({labels.config_prefix} {ctx.skill.version[:8]})"
    model = SummaryModel(
        brand=ctx.rendered(c.brand_name, "branding") if c.brand_name else None,
        account=c.account_name,
        as_of=c.as_of,
        domain=ctx.rendered(c.domain, "summary.title") if c.domain else None,
        version=ctx.rendered(version, "summary.title"),
        file_names=_file_names(ctx),
        grade_legend=_grade_legend(ctx),
        narrative=tuple(_block(ctx, s) for s in sections["narrative"]),
        stands_out=stands_out,
        lane_meanings=lane_meanings,
        caveats=tuple(_block(ctx, s) for s in sections.get("caveat", [])),
        sensitive_sentence=ctx.rules.sensitive_sentence.text,
        news=news,
        news_sentence=news_sentence,
        baseline=baseline,
        signals=signals,
        lanes=lanes,
        people_intro=tuple(people_intro),
        key_people=key_people,
        status_changes=status_changes,
        linkedin_findings=tuple(findings),
        executive_voice=tuple(voice),
        hypotheses=hypotheses,
        absences=absences,
        first_call_questions=tuple(_block(ctx, s) for s in sections.get("first_call_question", [])),
        decision_changes=tuple(_block(ctx, s) for s in sections.get("decision_change", [])),
        waiting=waiting,
        tracker=tracker,
        tracker_tally=tally,
        source_links=links,
        platform_hiring=hiring,
        none_found=ctx.rules.none_found.text,
        legal_name=_legal_name(ctx),
        document_name=_document_name(ctx),
        files_line=_files_line(ctx),
        tally_line=tally_line(ctx, tracker),
    )
    # rows in document order: the refs must match the keys re-read from the produced file (B3)
    keyed = [
        *((r.row_id, r.ref, digest(r)) for r in news),
        # the baseline is a bulleted list: its rows have no visible key, like the authored text
        *((r.row_id, "", digest(r)) for r in baseline),
        *((r.row_id, r.ref, digest(r)) for t in signals for r in t.rows),
        *((r.row_id, r.ref, digest(r)) for r in lane_rows),
        *((r.row_id, r.ref, digest(r)) for r in hiring),
        *((r.row_id, r.ref, digest(r)) for r in key_people),
        *((r.row_id, r.ref, digest(r)) for r in findings),
        *((r.row_id, r.ref, digest(r)) for r in status_changes),
        # the waiting rows are plain bullets (as the reference prints them): no visible key
        *((r.row_id, "", digest(r)) for r in waiting),
        *((r.row_id, r.ref, digest(r)) for r in tracker),
        *((f"link:{r.code}", r.ref, digest(r)) for r in links),
    ]
    keyed += [(b.row_id, "", digest(b)) for b in (*texts, *absences)]
    return Mapped(
        model,
        row_ids=tuple(k[0] for k in keyed),
        digests=tuple(k[2] for k in keyed),
        refs=tuple(k[1] for k in keyed if k[1]),
        cited=frozenset(cited),
    )
