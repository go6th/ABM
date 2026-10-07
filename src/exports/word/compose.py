"""Render models to ``DocumentSpec`` (pure). Section order is the PRD's (s3.4); headings, table
headers and fixed words come from the rules, proportions from the theme. An empty section gets the
explicit "None found" note (B3), never a blank."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from exports.config import ExportConfig, TableName
from exports.models import AuthoredBlock, EvidenceModel, LaneRow, SummaryModel, TrackerRow
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


def lane_cells(r: LaneRow) -> tuple[str, str, str, str, str, str]:
    """One lane-table row exactly as the Account Summary prints it (and as the client's Facts by
    grade table shows it, ``views.rowsources``): Ref, Finding, Grade, Use, Confirming question,
    Sources."""
    return (r.ref, r.finding, r.grade_label, r.use, r.question or "", ", ".join(r.codes))


def tracker_cells(t: TrackerRow) -> tuple[str, str, str, str, str, str, str]:
    """One coverage-tracker row exactly as the Account Summary prints it (and as the client's
    Coverage tracker table shows it): Ref, Coverage row, Status, Priority, Reason, Tools,
    Results."""
    return (t.ref, t.row, t.status, t.priority, t.reason or "", t.tools, t.results)


def _header(brand: str | None, account: str, doc_title: str) -> str:
    """The page header: the tenant's display name (VW6, when branded), the account, the title.
    The attribution stays in the footer whatever the branding (skill rule 6, D-64)."""
    return f"{brand}. {account}. {doc_title}" if brand else f"{account}. {doc_title}"


def _cited(text: str, codes: Sequence[str]) -> str:
    return f"{text} ({', '.join(codes)})" if codes else text


def _heading(text: str) -> HeadingBlock:
    return HeadingBlock(type="heading", level=1, text=text)


def _para(text: str, style: Literal["body", "quote", "note"] = "body") -> ParagraphBlock:
    return ParagraphBlock(type="paragraph", style=style, text=text)


def _blocks(items: Sequence[AuthoredBlock], none: str) -> list[Block]:
    if not items:
        return [_para(none, "note")]
    return [_para(_cited(b.text, b.codes)) for b in items]


def _frame(cfg: ExportConfig, title: str, account: str, as_of: str) -> tuple[str, str, str]:
    labels = cfg.rules.documents.labels
    subtitle = f"{account}. {labels.as_of} {as_of}. {labels.attribution_line}."
    return title, subtitle, f"{labels.attribution_line}. {title}."


def compose_summary(model: SummaryModel, cfg: ExportConfig, title: str) -> DocumentSpec:
    """The Account Summary document."""
    h = cfg.rules.headings.summary
    labels = cfg.rules.documents.labels
    none = model.none_found
    doc_title, subtitle, footer = _frame(cfg, title, model.account, model.as_of.isoformat())
    blocks: list[Block] = [TitleBlock(type="title", text=doc_title, subtitle=subtitle)]
    blocks += [_heading(h.narrative), *_blocks(model.narrative, none)]
    blocks += [_heading(h.caveats), *_blocks(model.caveats, none)]
    blocks += [
        HeadingBlock(type="heading", level=2, text=labels.sensitive_heading),
        _para(model.sensitive_sentence),
    ]
    blocks.append(_heading(h.lane_tables))
    if not model.lanes:
        blocks.append(_para(none, "note"))
    for lane in model.lanes:
        blocks.append(HeadingBlock(type="heading", level=2, text=lane.lane))
        blocks.append(
            _table(
                cfg,
                "lane",
                [lane_cells(r) for r in lane.rows],
            )
        )
    blocks.append(_heading(h.key_people))
    blocks.append(
        _table(
            cfg,
            "key_people",
            [
                (p.ref, p.name, p.title, p.level, p.lanes, p.profile_url, p.source_code)
                for p in model.key_people
            ],
        )
        if model.key_people
        else _para(none, "note")
    )
    blocks.append(_heading(h.linkedin_findings))
    blocks.append(
        _table(
            cfg,
            "linkedin",
            [
                (f.ref, f.person, f.claim, f.sentence, f.url, f.source_code)
                for f in model.linkedin_findings
            ],
        )
        if model.linkedin_findings
        else _para(none, "note")
    )
    blocks.append(_heading(h.executive_voice))
    if not model.executive_voice:
        blocks.append(_para(none, "note"))
    for q in model.executive_voice:
        blocks.append(_para(q.text, "quote"))
        blocks.append(_para(_cited(f"{labels.speaker_prefix}: {q.speaker}", q.codes), "note"))
    blocks.append(_heading(h.hypotheses))
    if not model.hypotheses and not model.absences:
        blocks.append(_para(none, "note"))
    for hyp in model.hypotheses:
        blocks.append(_para(_cited(hyp.text, hyp.codes)))
        blocks.append(_para(f"{labels.question_prefix}: {hyp.question}", "note"))
    if model.absences:
        blocks.append(HeadingBlock(type="heading", level=2, text=h.absence))
        for a in model.absences:
            blocks.append(_para(a.text))
            blocks.append(_para(f"{labels.question_prefix}: {a.question}", "note"))
    blocks.append(_heading(h.first_call_questions))
    blocks.append(
        BulletsBlock(
            type="bullets", items=tuple(_cited(b.text, b.codes) for b in model.first_call_questions)
        )
        if model.first_call_questions
        else _para(none, "note")
    )
    blocks += [_heading(h.decision_changes), *_blocks(model.decision_changes, none)]
    blocks.append(_heading(h.coverage_tracker))
    blocks += [_para(s, "note") for s in model.tracker_tally]
    blocks.append(
        _table(
            cfg,
            "tracker",
            [tracker_cells(t) for t in model.tracker],
        )
    )
    return DocumentSpec(
        theme=cfg.theme.word,
        header_text=_header(model.brand, model.account, doc_title),
        footer_text=footer,
        blocks=tuple(blocks),
    )


def compose_evidence(model: EvidenceModel, cfg: ExportConfig, title: str) -> DocumentSpec:
    """The Sources and Evidence document."""
    h = cfg.rules.headings.evidence
    labels = cfg.rules.documents.labels
    doc_title, subtitle, footer = _frame(cfg, title, model.account, model.as_of.isoformat())
    blocks: list[Block] = [TitleBlock(type="title", text=doc_title, subtitle=subtitle)]
    blocks.append(_heading(h.sources))
    rows = []
    for s in model.sources:
        uses = (
            "\n".join(f"{i}. {u.grade_label}. {u.sentence}" for i, u in enumerate(s.uses, start=1))
            or labels.no_sentence
        )
        date = s.published.isoformat() if s.published else labels.not_dated
        rows.append((s.ref, s.code, s.url, date, s.kind_label, uses))
    blocks.append(_table(cfg, "sources", rows) if rows else _para(model.none_found, "note"))
    blocks.append(_heading(h.job_descriptions))
    blocks.append(
        _table(cfg, "job_reads", [(j.ref, j.title, j.url, j.code) for j in model.job_descriptions])
        if model.job_descriptions
        else _para(model.none_found, "note")
    )
    blocks.append(_heading(h.news))
    blocks.append(
        _table(
            cfg,
            "news",
            [
                (
                    n.ref,
                    n.sentence,
                    n.url,
                    n.code,
                    labels.verified if n.verified else labels.feed_only,
                )
                for n in model.news
            ],
        )
        if model.news
        else _para(model.none_found, "note")
    )
    return DocumentSpec(
        theme=cfg.theme.word,
        header_text=_header(model.brand, model.account, doc_title),
        footer_text=footer,
        blocks=tuple(blocks),
    )
