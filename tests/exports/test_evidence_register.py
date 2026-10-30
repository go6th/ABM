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
from exports.word.contract import Run, TableBlock, TitleBlock, cell_hrefs, cell_text
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
        update={"entity_lock": {"legal_name": "Synthetic Holdings Company"}}
    )
    model, _ = _mapped(c, cfg, grading)
    spec = compose_evidence(model, cfg, "BD Sources and Evidence")
    title = spec.blocks[0]
    assert isinstance(title, TitleBlock)
    assert title.text == "BD (Synthetic Holdings Company): Sources and Evidence"
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
    # the Source cell is the stored title linked to the page; without a title the URL is the
    # link text; no archived copy stored, so no second line
    assert first[2] == (Run(text="Synthetic page title", href=sources[0].url),)
    assert rows["S-0002"][2] == (Run(text=sources[1].url, href=sources[1].url),)
    assert cell_hrefs(first[2]) == (sources[0].url,)
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
    posting = next(r for r in rows.values() if "/jobs/" in cell_text(r[2]))
    assert cell_hrefs(posting[2]) == (cell_text(posting[2]),)  # the posting link, as text
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
        (Run(text=f"{j.title} ({status}, first seen {j.first_seen})", href=j.url),),
        "Salesforce",
        cfg.rules.labels.posting_class["admin_owner"],
        j.sentences,
        j.entity,
    )
    assert j.entity  # the posting's entity, else the account


def _archived(c: SnapshotContent) -> SnapshotContent:
    """Source S-0001 and posting post-00000 with an archived copy; the rest without."""
    sources = tuple(
        s.model_copy(update={"archive_url": "https://archive.example/copies/S-0001"})
        if s.code == "S-0001"
        else s
        for s in c.sources
    )
    postings = tuple(
        p.model_copy(update={"archive_url": "https://archive.example/copies/post-00000"})
        if p.posting_key == "post-00000"
        else p
        for p in c.postings
    )
    return c.model_copy(update={"sources": sources, "postings": postings})


def test_an_archived_copy_is_linked_under_the_source_and_the_posting(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    """The owner's rule: every link has its archived copy beside it. A source or posting with a
    stored copy carries a second linked line "Archived copy" in parts A, B and C and an
    "archived:" line in Part I; one without a copy carries nothing extra."""
    labels = cfg.rules.documents.labels
    model, _ = _mapped(_archived(account()), cfg, grading)
    spec = compose_evidence(model, cfg, "T")
    sources = {r[1]: r[2] for r in _table(spec, "sources").rows}
    archived, plain = sources["S-0001"], sources["S-0002"]
    assert cell_hrefs(archived) == (
        "https://alpha.example/news/item-0",
        "https://archive.example/copies/S-0001",
    )
    assert cell_text(archived) == f"https://alpha.example/news/item-0\n{labels.archived_copy}"
    assert len(cell_hrefs(plain)) == 1 and labels.archived_copy not in cell_text(plain)
    reads = {r[0]: r[1] for r in _table(spec, "job_reads").rows}
    by_key = {j.ref: j for j in model.job_descriptions}
    first = next(ref for ref, j in by_key.items() if j.row_id == "post-00000")
    assert cell_hrefs(reads[first])[-1] == "https://archive.example/copies/post-00000"
    others = [cell_hrefs(v) for k, v in reads.items() if k != first]
    assert others and all(len(h) == 1 for h in others)
    lane = _table(spec, "lane_reads")
    lane_rows = {r[0]: r[1] for r in lane.rows}
    linked = [cell_hrefs(v) for v in lane_rows.values()]
    assert sum(1 for h in linked if len(h) == 2) == 1 and all(h for h in linked)
    # Part I: the plain line, then the archived address under it
    texts = [b.text for b in spec.blocks if getattr(b, "style", None) == "note"]
    at = texts.index(labels.url_line.format(code="S-0001", url="https://alpha.example/news/item-0"))
    assert texts[at + 1] == labels.archived_line.format(url="https://archive.example/copies/S-0001")
    assert texts[at + 2] == labels.url_line.format(
        code="S-0002", url="https://alpha.example/news/item-1"
    )
    # no link in a file is a bare URL printed as text: every Source and Posting cell links
    for name in ("sources", "job_reads", "lane_reads"):
        for row in _table(spec, name).rows:
            assert cell_hrefs(row[2 if name == "sources" else 1]), name
