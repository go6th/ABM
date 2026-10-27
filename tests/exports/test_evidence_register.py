"""Sources and Evidence opening, Part A (source register) and Part B (postings read in full) in
the shape of the owner's reference file (BD_Sources_and_Evidence.docx, 2026-10-20): the title
and opening line, the Source cell as title above link, the Type, Date and How retrieved words,
What it supports as one line per finding with its grade when not Confirmed, and Part B's columns
Posting, Platforms, Class, What the posting says, Entity with the newest reads first."""

from __future__ import annotations

from datetime import date
from typing import Any

from account_intelligence.grading.config import GradingConfig
from exports.builder import map_snapshot
from exports.config import ExportConfig
from exports.content import SnapshotContent
from exports.readers.evidence import _source_type
from exports.word.compose import compose_evidence
from exports.word.contract import TableBlock, TitleBlock
from tests.exports.corpus import account


def _mapped(content: SnapshotContent, cfg: ExportConfig, grading: GradingConfig) -> Any:
    mapped, ctx = map_snapshot(content, cfg, grading)
    assert not ctx.violations, ctx.violations
    return mapped["evidence"].model, ctx


def _table(spec: Any, name: str) -> TableBlock:
    return next(b for b in spec.blocks if isinstance(b, TableBlock) and b.name == name)


def test_headers_follow_the_reference(cfg: ExportConfig) -> None:
    tables = cfg.rules.documents.tables
    assert tuple(tables["sources"][2:]) == (
        "Source",
        "Type",
        "Date",
        "How retrieved",
        "What it supports",
    )
    assert tuple(tables["job_reads"]) == (
        "Ref",
        "Posting",
        "Platforms",
        "Class",
        "What the posting says",
        "Entity",
    )
    assert len(cfg.theme.word.tables["job_reads"]) == len(tables["job_reads"])


def test_title_and_opening_line(cfg: ExportConfig, grading: GradingConfig) -> None:
    c = account(name="BD").model_copy(
        update={"entity_lock": {"legal_name": "Becton, Dickinson and Company"}}
    )
    model, _ = _mapped(c, cfg, grading)
    spec = compose_evidence(model, cfg, "BD Sources and Evidence")
    title = spec.blocks[0]
    assert isinstance(title, TitleBlock)
    assert title.text == "BD (Becton, Dickinson and Company): Sources and Evidence"
    day = c.as_of
    assert title.subtitle.startswith(f"Research date {day.day} {day:%B %Y}. Companion to BD_")
    assert "Account_Summary.docx" in title.subtitle
    assert title.subtitle.endswith("Research by 6th agents.")
    plain, _ = _mapped(account(name="BD"), cfg, grading)
    assert plain.doc_title == "BD: Sources and Evidence"


def test_part_a_cells(cfg: ExportConfig, grading: GradingConfig) -> None:
    c = account()
    sources = tuple(
        s.model_copy(update={"title": "Synthetic page title"}) if s.code == "S-0001" else s
        for s in c.sources
    )
    model, _ = _mapped(c.model_copy(update={"sources": sources}), cfg, grading)
    labels = cfg.rules.documents.labels
    rows = {r[1]: r for r in _table(compose_evidence(model, cfg, "T"), "sources").rows}
    first = rows["S-0001"]
    assert first[2] == f"Synthetic page title\n{sources[0].url}"  # title above the link
    assert rows["S-0002"][2] == sources[1].url  # no stored title: the link alone
    assert first[3] == labels.source_type_site  # the account's own domain
    published = sources[0].published
    assert published is not None
    assert first[4] == f"{published.day} {published:%b %Y}"
    assert first[5] == labels.retrieved_live == "Page read"
    assert rows["S-0002"][5] == labels.retrieved_snippet == "Search excerpt"
    # a Confirmed finding has no grade prefix; a Probable one carries it, then the sentence
    assert first[6] == (
        f"Synthetic finding 0 for {c.account_name}. Source text: Synthetic sentence 0 relied on."
    )
    assert rows["S-0002"][6].startswith(f"Probable: Synthetic finding 1 for {c.account_name}.")
    assert rows["S-0002"][6].endswith("Source text: Synthetic sentence 1 relied on.")
    posting = next(r for r in rows.values() if "/jobs/" in r[2])
    assert posting[3] == labels.source_type_posting
    # a posting's own link that no finding rests on names its workbook row
    assert (
        posting[6].startswith("Job Postings workbook row J-")
        and "Synthetic Salesforce" in (posting[6])
    )


def test_source_type_from_the_link(cfg: ExportConfig, grading: GradingConfig) -> None:
    _, ctx = _mapped(account(), cfg, grading)
    labels = cfg.rules.documents.labels
    assert _source_type(ctx, "https://www.linkedin.com/in/someone", "secondary", False) == (
        labels.source_type_profile
    )
    assert _source_type(ctx, "https://www.linkedin.com/posts/x-123", "secondary", False) == (
        labels.source_type_post
    )
    assert _source_type(ctx, "https://news.alpha.example/a", "primary", False) == (
        labels.source_type_site
    )
    assert _source_type(ctx, "https://elsewhere.example/a", "secondary", False) == ""
    assert _source_type(ctx, "https://alpha.example/a", "aggregator", False) == ""
    assert _source_type(ctx, "https://elsewhere.example/j", "primary", True) == (
        labels.source_type_posting
    )


def test_part_b_cells_and_order(cfg: ExportConfig, grading: GradingConfig) -> None:
    c = account(n_postings=5)
    model, _ = _mapped(c, cfg, grading)
    reads = model.job_descriptions
    # reads are posts 0, 2 and 4, all admin and owner roles: newest first seen first
    seen = [date.fromisoformat(j.first_seen) for j in reads]
    assert seen == sorted(seen, reverse=True) and len(reads) == 3
    assert [j.ref for j in reads] == ["JD-0001", "JD-0002", "JD-0003"]
    rows = _table(compose_evidence(model, cfg, "T"), "job_reads").rows
    j = reads[0]
    status = j.status.lower()
    assert rows[0] == (
        j.ref,
        f"{j.title} ({status}, first seen {j.first_seen})\n{j.url}",
        "Salesforce",
        cfg.rules.labels.posting_class["admin_owner"],
        j.sentences,
        j.entity,
    )
    assert j.entity  # the posting's entity, else the account
