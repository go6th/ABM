"""Render models to ``DocumentSpec`` (pure). The section order is the owner's output specification
(master prompt v2, 2026-10-24: section 9.1 for the Account Summary, 9.2 for Sources and Evidence);
headings, table headers and fixed words come from the rules, proportions from the theme. An empty
section gets the explicit "None found" note (B3), never a blank."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Literal

from exports.config import DocumentLabels, ExportConfig, TableName
from exports.models import (
    AuthoredBlock,
    BaselineRow,
    EvidenceModel,
    EvidenceUse,
    ExtractQuote,
    HypothesisBlock,
    JobRead,
    LaneRow,
    SummaryModel,
    TrackerRow,
    WaitingRow,
)
from exports.word.contract import (
    Block,
    BulletsBlock,
    DocumentSpec,
    HeadingBlock,
    ParagraphBlock,
    TableBlock,
    TitleBlock,
)


def _table(cfg: ExportConfig, name: TableName, rows: Sequence[Sequence[str]]) -> TableBlock:
    return TableBlock(
        type="table",
        name=name,
        headers=cfg.rules.documents.tables[name],
        widths=cfg.theme.word.tables[name],
        rows=tuple(tuple(r) for r in rows),
        keys=tuple(r[0] for r in rows),
    )


def picture_cells(r: LaneRow) -> tuple[str, str, str, str, str, str]:
    """One row of the Summary's Platform picture by lane (the reference Summary's section 5),
    exactly as the client's Facts by grade table shows it (``views.rowsources``): Ref, Item,
    What we found, Grade, Use in outreach, Reasoning and sources (how the finding was read, the
    codes, and the confirming question when it is asked)."""
    return (r.ref, r.item, r.finding, r.grade_label, r.use, r.reasoning or ", ".join(r.codes))


def baseline_line(b: BaselineRow) -> str:
    """One baseline bullet as the reference Summary prints it: ``Item: what (S-0001).``; a
    finding the policy does not let the file state names its grade with the sources."""
    what = b.what.rstrip().rstrip(".")
    cite = ", ".join(b.codes) if b.may_state else f"{b.grade_label}; {', '.join(b.codes)}"
    return f"{b.item}: {what} ({cite})."


def tracker_cells(t: TrackerRow) -> tuple[str, str, str, str, str, str, str]:
    """One coverage row as the client's on-screen Coverage tracker table shows it: Ref, Coverage
    row, Status, Priority, Reason, Tools, Results."""
    return (t.ref, t.row, t.status, t.priority, t.reason or "", t.tools, t.results)


def coverage_cells(t: TrackerRow) -> tuple[str, str, str, str, str, str, str]:
    """One coverage row as the Account Summary prints it (9.1 item 11): Ref, Source type, Status,
    Priority, Needs, What was done, What remains."""
    return (t.ref, t.row, t.status, t.priority, t.needs, t.done, t.remains)


def _header(brand: str | None, account: str, doc_title: str) -> str:
    """The page header: the tenant's display name (VW6, when branded), the account, the title.
    The attribution stays in the footer whatever the branding (skill rule 6, D-64)."""
    return f"{brand}. {account}. {doc_title}" if brand else f"{account}. {doc_title}"


def _cited(text: str, codes: Sequence[str]) -> str:
    return f"{text} ({', '.join(codes)})" if codes else text


def _extract_note(q: ExtractQuote, cite: bool, labels: DocumentLabels) -> str:
    """The line under a Part D extract: the speaker (an executive) or the author with the post's
    type and date, the grade, what it supports, and the extract's own code when its group spans
    several sources (the listed people's posts)."""
    parts: list[str] = []
    if q.speaker:
        parts.append(q.speaker if q.kind else f"{labels.speaker_prefix}: {q.speaker}")
    if q.kind:
        parts.append(f"{q.kind}, {q.dated}" if q.dated else q.kind)
    if q.grade_label:
        parts.append(q.grade_label)
    if q.supports:
        parts.append(f"{labels.supports_prefix}: {q.supports}")
    return _cited(". ".join(parts), (q.code,) if cite else ())


def _heading(text: str) -> HeadingBlock:
    return HeadingBlock(type="heading", level=1, text=text)


def _sub(text: str) -> HeadingBlock:
    return HeadingBlock(type="heading", level=2, text=text)


def _para(text: str, style: Literal["body", "quote", "note"] = "body") -> ParagraphBlock:
    return ParagraphBlock(type="paragraph", style=style, text=text)


def _blocks(items: Sequence[AuthoredBlock], none: str) -> list[Block]:
    if not items:
        return [_para(none, "note")]
    return [_para(_cited(b.text, b.codes)) for b in items]


def _bullets(items: Sequence[AuthoredBlock], none: str) -> Block:
    if not items:
        return _para(none, "note")
    return BulletsBlock(type="bullets", items=tuple(_cited(b.text, b.codes) for b in items))


def _table_or_none(
    cfg: ExportConfig, name: TableName, rows: Sequence[Sequence[str]], none: str
) -> Block:
    return _table(cfg, name, rows) if rows else _para(none, "note")


def _frame(
    cfg: ExportConfig, title: str, account: str, as_of: str, domain: str | None = None
) -> tuple[str, str, str]:
    labels = cfg.rules.documents.labels
    where = f" {labels.domain_prefix}: {domain}." if domain else ""
    subtitle = f"{account}.{where} {labels.as_of} {as_of}. {labels.research_by}."
    return title, subtitle, f"{labels.attribution_line}. {title}."


def _long_date(d: date) -> str:
    """``2026-10-20`` -> ``20 October 2026``, as the reference opening writes the research date."""
    return f"{d.day} {d:%B %Y}"


def _summary_title(model: SummaryModel, cfg: ExportConfig, title: str) -> str:
    """ "BD (Becton, Dickinson and Company): Account Summary" as the reference titles it; the
    file title when the document's own name is not configured."""
    labels = cfg.rules.documents.labels
    if not model.document_name:
        return title
    if model.legal_name:
        return labels.summary_title.format(
            account=model.account, legal_name=model.legal_name, document=model.document_name
        )
    return labels.summary_title_plain.format(account=model.account, document=model.document_name)


def _title_block(model: SummaryModel, cfg: ExportConfig, title: str) -> list[Block]:
    """The reference Summary's opening: the title, then one paragraph with the research date,
    the other four files by name and the attribution (no heading, no version or domain line;
    the run version stays in the manifest and the model data). The pursuing client sentence of
    the reference is left out: the snapshot does not carry the run's pursuing client."""
    labels = cfg.rules.documents.labels
    parts = [labels.research_date.format(date=_long_date(model.as_of))]
    if model.files_line:
        parts.append(model.files_line)
    elif model.file_names:
        parts.append(f"{labels.files_intro} {'; '.join(model.file_names)}.")
    parts.append(f"{labels.research_by}.")
    opening = " ".join(parts)
    return [TitleBlock(type="title", text=_summary_title(model, cfg, title), subtitle=opening)]


def _tracker_blocks(model: SummaryModel, cfg: ExportConfig) -> list[Block]:
    """9.1 item 11: decisions waiting on the account owner, the pass 1 and pass 2 tables, the
    tally computed from the rows."""
    h = cfg.rules.headings.summary
    none = model.none_found
    blocks: list[Block] = [_heading(h.coverage_tracker), _sub(h.tracker_waiting)]
    # the reference lists the waiting decisions as bullets, one sentence each
    blocks.append(
        BulletsBlock(type="bullets", items=tuple(waiting_line(w, cfg) for w in model.waiting))
        if model.waiting
        else _para(none, "note")
    )
    for pass_no, title in ((1, h.tracker_pass1), (2, h.tracker_pass2)):
        rows = [coverage_cells(t) for t in model.tracker if t.pass_no == pass_no]
        blocks.append(_sub(title))
        blocks.append(_table_or_none(cfg, "coverage", rows, none))
    # one tally line over both tables, as the reference closes the tracker
    if model.tally_line:
        blocks.append(_para(model.tally_line))
    else:
        blocks += [_para(s, "note") for s in model.tracker_tally]
    return blocks


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def waiting_line(w: WaitingRow, cfg: ExportConfig) -> str:
    """One decision waiting on the account owner as the reference words it: the row, its state
    and what it needs ("Platform vendor account team: blocked, waiting on account owner.")."""
    labels = cfg.rules.documents.labels
    status = _lower_first(w.status) if w.status else ""
    if not w.needs or w.needs == labels.needs_nothing:
        return labels.waiting_line_plain.format(row=w.row, status=status)
    return labels.waiting_line.format(row=w.row, status=status, needs=_lower_first(w.needs))


def _numbered_with_questions(
    items: Sequence[HypothesisBlock], prefix: str, cite: bool
) -> BulletsBlock:
    """A numbered list of hypotheses (or absences), each with "Confirming question: ..." as an
    indented line under it, the reference Summary's section 7 layout."""
    return BulletsBlock(
        type="bullets",
        items=tuple(_cited(x.text, x.codes) if cite else x.text for x in items),
        numbered=True,
        notes=tuple(f"{prefix}: {x.question}" for x in items),
    )


def compose_summary(model: SummaryModel, cfg: ExportConfig, title: str) -> DocumentSpec:
    """The Account Summary document, sections 1 to 12 of master prompt v2 section 9.1."""
    h = cfg.rules.headings.summary
    labels = cfg.rules.documents.labels
    none = model.none_found
    doc_title, _, footer = _frame(cfg, title, model.account, model.as_of.isoformat())
    blocks: list[Block] = _title_block(model, cfg, doc_title)
    # how to read (the line on source IDs, then the grade legend)
    blocks.append(_heading(h.how_to_read))
    blocks.append(_para(labels.legend_intro))
    blocks.append(
        _table_or_none(
            cfg, "grade_legend", [(g.grade, g.meaning, g.use) for g in model.grade_legend], none
        )
    )
    # 1 the account in plain words, as the reference Summary lays it out: the picture
    # (paragraphs), what stands out (bullets), what it likely means for each lane (one labeled
    # paragraph per lane), how this could be wrong (bullets), the simplest way to sharpen it,
    # then the executives' own words
    blocks += [_heading(h.narrative), _sub(h.narrative_picture), *_blocks(model.narrative, none)]
    blocks += [_sub(h.stands_out), _bullets(model.stands_out, none)]
    blocks.append(_sub(h.lane_meaning))
    if not model.lane_meanings:
        blocks.append(_para(none, "note"))
    blocks += [_para(_cited(f"{b.lane}. {b.text}", b.codes)) for b in model.lane_meanings]
    blocks += [_sub(h.caveats), _bullets(model.caveats, none)]
    blocks += [_sub(h.decision_changes), *_blocks(model.decision_changes, none)]
    blocks.append(_sub(h.executive_voice))
    if not model.executive_voice:
        blocks.append(_para(none, "note"))
    for q in model.executive_voice:
        blocks.append(_para(q.text, "quote"))
        blocks.append(_para(_cited(f"{labels.speaker_prefix}: {q.speaker}", q.codes), "note"))
    # 2 recent news
    blocks.append(_heading(h.news))
    blocks.append(
        _table_or_none(
            cfg,
            "recent_news",
            [(n.ref, n.date, n.what, n.read, ", ".join(n.codes), n.why) for n in model.news],
            none,
        )
    )
    if model.news_sentence:
        blocks.append(_para(model.news_sentence, "note"))
    # 3 baseline: one bullet per item, as the reference Summary lays it out
    blocks.append(_heading(h.baseline))
    blocks.append(
        BulletsBlock(type="bullets", items=tuple(baseline_line(b) for b in model.baseline))
        if model.baseline
        else _para(none, "note")
    )
    # 4 signals: the tier is the subheading, the rows Finding, Grade, Lanes, Date, Sources
    blocks.append(_heading(h.signals))
    if not model.signals:
        blocks.append(_para(none, "note"))
    for table in model.signals:
        blocks.append(_sub(table.title))
        blocks.append(
            _table_or_none(
                cfg,
                "signals",
                [
                    (s.ref, s.finding, s.grade_label, s.lanes, s.date, ", ".join(s.codes))
                    for s in table.rows
                ],
                none,
            )
        )
    # 5 platform picture by lane
    # one table of every lane row in lane order, as the reference Summary prints it
    blocks.append(_heading(h.lane_tables))
    blocks.append(
        _table_or_none(
            cfg,
            "platform_picture",
            [picture_cells(r) for lane in model.lanes for r in lane.rows],
            none,
        )
    )
    if model.platform_hiring:
        blocks.append(_sub(h.platform_hiring))
        hiring = _table(
            cfg,
            "platform_hiring",
            [
                (
                    r.ref,
                    r.platform,
                    str(r.postings),
                    str(r.open_now),
                    str(r.build),
                    str(r.admin_owner),
                    str(r.working_tool),
                    str(r.user),
                )
                for r in model.platform_hiring
            ],
        )
        # "Open on 19 Oct": the open column names the as-of date, as the reference does
        on = f"{model.as_of.day} {model.as_of:%b}"
        headers = tuple(x.replace("{as_of}", on) for x in hiring.headers)
        blocks.append(hiring.model_copy(update={"headers": headers}))
    # 6 people: how the list was built, key people, findings from profiles, status changes
    blocks.append(_heading(h.people))
    blocks.append(_sub(h.people_method))
    blocks += [_para(s) for s in model.people_intro] or [_para(none, "note")]
    blocks.append(_sub(h.key_people))
    blocks.append(
        _table_or_none(
            cfg,
            "key_people",
            [
                (
                    p.ref,
                    p.name,
                    p.title,
                    p.lanes,
                    p.priority,
                    p.profile_url,
                    p.note,
                    p.source_code,
                )
                for p in model.key_people
            ],
            none,
        )
    )
    blocks.append(_sub(h.linkedin_findings))
    blocks.append(
        _table_or_none(
            cfg,
            "linkedin",
            [
                (f.ref, f.person, f.claim, f.sentence, f.source_code)
                for f in model.linkedin_findings
            ],
            none,
        )
    )
    blocks.append(_sub(h.status_changed))
    blocks.append(
        _table_or_none(
            cfg,
            "status_changed",
            [
                (s.ref, s.person, s.search_title, s.profile_title, s.source_code)
                for s in model.status_changes
            ],
            none,
        )
    )
    # 7 hypotheses to test: a numbered list, each hypothesis with its confirming question under
    # it (build_summary.js line 93); the cautions under them as bullets
    blocks.append(_heading(h.hypotheses))
    if not model.hypotheses and not model.absences:
        blocks.append(_para(none, "note"))
    if model.hypotheses:
        blocks.append(_numbered_with_questions(model.hypotheses, labels.question_prefix, True))
    if model.absences:
        blocks.append(_sub(h.absence))
        blocks.append(_numbered_with_questions(model.absences, labels.question_prefix, False))
    blocks.append(_sub(h.sensitive_cautions))
    blocks.append(BulletsBlock(type="bullets", items=(model.sensitive_sentence,)))
    # 8 questions for a first conversation: a numbered list
    blocks.append(_heading(h.first_call_questions))
    blocks.append(
        BulletsBlock(
            type="bullets",
            items=tuple(_cited(b.text, b.codes) for b in model.first_call_questions),
            numbered=True,
        )
        if model.first_call_questions
        else _para(none, "note")
    )
    # 9 research coverage tracker
    blocks += _tracker_blocks(model, cfg)
    # 10 source links, then the line on where the postings read are linked
    blocks.append(_heading(h.source_links))
    blocks.append(
        _table_or_none(
            cfg,
            "source_links",
            [(s.ref, s.code, s.url, s.domain) for s in model.source_links],
            none,
        )
    )
    blocks.append(_para(labels.source_links_note))
    return DocumentSpec(
        theme=cfg.theme.word,
        header_text=_header(model.brand, model.account, doc_title),
        footer_text=footer,
        blocks=tuple(blocks),
    )


def _lane_read_blocks(model: EvidenceModel, cfg: ExportConfig, none: str) -> list[Block]:
    """Evidence part C as the reference prints it: per lane platform a sub-heading with the
    counts, then its postings read in full (Ref, Posting, Class, Status, Classification
    reasoning), or a note when none was read; one closing note. "None found" without lanes."""
    rules = cfg.rules.evidence
    if not model.lane_reads:
        return [_para(none, "note")]
    blocks: list[Block] = []
    for g in model.lane_reads:
        blocks.append(_sub(g.heading))
        rows = [
            (
                r.ref,
                rules.lane_read_posting.format(
                    title=r.title, posting_ref=r.posting_ref, first_seen=r.first_seen
                )
                + f"\n{r.url}",
                r.posting_class,
                r.status,
                r.reasoning,
            )
            for r in g.rows
        ]
        blocks.append(_table(cfg, "lane_reads", rows) if rows else _para(rules.lane_read_none))
    blocks.append(_para(rules.lane_read_note, "note"))
    return blocks


_SUMMARY_FALLBACK = "the Account Summary"
_CONFIRMED = "Confirmed"


def _supports(uses: Sequence[EvidenceUse], quote_label: str) -> str:
    """Part A "What it supports": one line per finding the source supports, in the order the
    sentences were relied on; a finding not graded Confirmed carries its grade first (the
    reference writes "Lead: ..."), then the sentence it rests on (withheld note when held)."""
    order: list[str] = []
    grouped: dict[str, list[EvidenceUse]] = {}
    for u in uses:
        key = u.fact_id or u.row_id
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(u)
    lines = []
    for key in order:
        first = grouped[key][0]
        prefix = "" if first.grade_label == _CONFIRMED else f"{first.grade_label}: "
        quotes = " ".join(u.sentence for u in grouped[key])
        finding = first.finding.rstrip(". ")
        body = f"{finding}. {quote_label}: {quotes}" if finding else f"{quote_label}: {quotes}"
        lines.append(prefix + body)
    return "\n".join(lines)


def _posting_name(j: JobRead, first_seen: str) -> str:
    """Part B posting cell, as the reference writes it: "Title (open, first seen 2026-10-19)"."""
    parts = [j.status.lower()] if j.status else []
    if j.first_seen:
        parts.append(first_seen.format(date=j.first_seen))
    return f"{j.title} ({', '.join(parts)})" if parts else j.title


def compose_evidence(model: EvidenceModel, cfg: ExportConfig, title: str) -> DocumentSpec:
    """The Sources and Evidence document, parts A to I of master prompt v2 section 9.2."""
    h = cfg.rules.headings.evidence
    labels = cfg.rules.documents.labels
    none = model.none_found
    _, _, footer = _frame(cfg, title, model.account, model.as_of.isoformat())
    # the reference opening: "BD (...): Sources and Evidence", then one line with the research
    # date, the companion Summary file and the attribution
    blocks: list[Block] = [
        TitleBlock(
            type="title",
            text=model.doc_title or title,
            subtitle=labels.evidence_opening.format(
                date=_long_date(model.as_of),
                summary=model.summary_file or _SUMMARY_FALLBACK,
            ),
        )
    ]
    # A source register
    blocks.append(_heading(h.sources))
    rows = []
    for s in model.sources:
        date = f"{s.published.day} {s.published:%b %Y}" if s.published else labels.not_dated
        rows.append(
            (
                s.ref,
                s.code,
                f"{s.title}\n{s.url}" if s.title else str(s.url),
                s.type_label or s.kind_label,
                date,
                s.retrieved or labels.retrieved_link,
                _supports(s.uses, labels.supports_quote) or s.supports_note or labels.no_sentence,
            )
        )
    blocks.append(_table_or_none(cfg, "sources", rows, none))
    # B job posting extracts for postings read in full
    blocks.append(_heading(h.job_descriptions))
    if model.job_descriptions:
        blocks.append(_para(labels.job_reads_intro.format(account=model.account)))
    blocks.append(
        _table_or_none(
            cfg,
            "job_reads",
            [
                (
                    j.ref,
                    f"{_posting_name(j, labels.posting_first_seen)}\n{j.url}",
                    j.platforms,
                    j.posting_class,
                    j.sentences or labels.no_sentence,
                    j.entity,
                )
                for j in model.job_descriptions
            ],
            none,
        )
    )
    # C postings read one by one, by lane
    blocks.append(_heading(h.careers_postings))
    blocks += _lane_read_blocks(model, cfg, none)
    # D video, article and release extracts: one sub-heading per source read in depth, its link,
    # then each verbatim extract with who said or wrote it, its grade and what it supports
    blocks.append(_heading(h.post_extracts))
    if not model.extracts:
        blocks.append(_para(none, "note"))
    for group in model.extracts:
        blocks.append(_sub(group.heading))
        if group.url:
            blocks.append(_para(group.url))
        for q in group.quotes:
            blocks.append(_para(q.text, "quote"))
            blocks.append(_para(_extract_note(q, group.code is None, labels), "note"))
    # E job board captures
    blocks.append(_heading(h.board_captures))
    if model.board_captures or model.live_heading is None:
        blocks.append(
            _table_or_none(
                cfg,
                "board_captures",
                [(b.ref, b.keyword, str(b.matches), b.read) for b in model.board_captures],
                none,
            )
        )
    if model.live_heading is not None:
        blocks.append(_sub(model.live_heading))
        blocks.append(
            _table_or_none(
                cfg,
                "live_postings",
                [
                    (p.ref, p.posting, p.platforms, p.entity, p.read, p.posting_class)
                    for p in model.live_postings
                ],
                none,
            )
        )
    # F leads not verified, and items checked and dropped
    blocks.append(_heading(h.leads))
    blocks.append(
        _table_or_none(
            cfg,
            "leads",
            [(x.ref, x.lead, x.queries, x.grade_label, ", ".join(x.codes)) for x in model.leads],
            none,
        )
    )
    # G how to keep copies of the original pages
    blocks.append(_heading(h.keep_copies))
    blocks.append(_para(model.keep_copies or none, "body" if model.keep_copies else "note"))
    # H data pulls
    blocks.append(_heading(h.data_pulls))
    blocks.append(
        _table_or_none(
            cfg,
            "data_pulls",
            [(d.ref, d.pull, str(d.count), d.base) for d in model.data_pulls],
            none,
        )
    )
    blocks.append(_sub(h.tech_detections))
    if model.detections:
        blocks.append(_para(labels.detections_intro))
    blocks.append(
        _table_or_none(
            cfg,
            "tech_detections",
            [
                (d.ref, d.technology, d.first_seen, d.last_seen, str(d.postings))
                for d in model.detections
            ],
            none,
        )
    )
    blocks.append(_sub(h.platform_postings))
    blocks.append(
        _table_or_none(
            cfg,
            "platform_postings",
            [
                (
                    p.ref,
                    p.platform,
                    str(p.postings),
                    str(p.open_now),
                    str(p.build),
                    str(p.admin_owner),
                    str(p.working_tool),
                    str(p.user),
                )
                for p in model.platform_postings
            ],
            none,
        )
    )
    blocks.append(_sub(h.entity_split))
    blocks.append(
        _table_or_none(
            cfg,
            "entity_split",
            [(e.ref, e.entity, str(e.postings)) for e in model.entity_split],
            none,
        )
    )
    blocks.append(_sub(h.news_feed))
    blocks.append(_para(model.news_feed_note or none, "body" if model.news_feed_note else "note"))
    blocks.append(_sub(h.teams))
    blocks.append(_para(model.teams_note or none, "note"))
    # I full URLs written out
    blocks.append(_heading(h.urls))
    blocks.append(_table_or_none(cfg, "urls", [(u.ref, u.code, u.url) for u in model.urls], none))
    return DocumentSpec(
        theme=cfg.theme.word,
        header_text=_header(model.brand, model.account, title),
        footer_text=footer,
        blocks=tuple(blocks),
    )
