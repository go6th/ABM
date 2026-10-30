"""Sources and Evidence parts A to I (master prompt v2 section 9.2): the register with type and
retrieval, postings read in depth, the same by lane platform, post extracts, board captures, Leads,
the keep-copies note, data pulls and the URLs written out. Parts without data say "None found";
no people list is printed."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from account_intelligence.grading.config import GradingConfig
from exports.builder import ExportBuilder, map_snapshot
from exports.config import ExportConfig
from exports.content import PullCall, SnapshotContent
from exports.verify import docx_text
from tests.exports.corpus import account, sparse
from tests.fakes.exports import MemoryStore

asyncio_test = pytest.mark.asyncio
Make = Callable[..., ExportBuilder]
PARTS = (
    "sources",
    "job_descriptions",
    "careers_postings",
    "post_extracts",
    "board_captures",
    "leads",
    "keep_copies",
    "data_pulls",
    "urls",
)


def evidence_of(content: SnapshotContent, cfg: ExportConfig, grading: GradingConfig) -> Any:
    mapped, ctx = map_snapshot(content, cfg, grading)
    assert not ctx.violations, ctx.violations
    return mapped["evidence"]


async def evidence_text(make: Make, content: SnapshotContent) -> str:
    store = MemoryStore()
    result = await make(content, store).build_all(content.tenant_id, content.run_id)
    assert result.status == "complete", result.errors
    d = next(x for x in result.deliverables if x.kind == "evidence")
    return docx_text(store.objects[d.object_key])


@asyncio_test
async def test_parts_a_to_i_in_order_and_no_people_list(
    make_builder: Make, cfg: ExportConfig
) -> None:
    text = await evidence_text(make_builder, account())
    h = cfg.rules.headings.evidence
    positions = [text.find(getattr(h, key)) for key in PARTS]
    assert all(p >= 0 for p in positions) and positions == sorted(positions)
    assert cfg.rules.headings.summary.key_people not in text
    assert cfg.rules.documents.labels.keep_copies_text in text
    for banned in ("—", "–"):
        assert banned not in text


def test_part_a_register_has_type_and_retrieved(cfg: ExportConfig, grading: GradingConfig) -> None:
    model = evidence_of(account(), cfg, grading).model
    labels = cfg.rules.documents.labels
    by_code = {s.code: s for s in model.sources}
    assert by_code["S-0001"].retrieved == labels.retrieved_live  # full page read
    assert by_code["S-0002"].retrieved == labels.retrieved_snippet  # search snippet
    assert by_code["S-0007"].retrieved == labels.retrieved_link  # a profile, cited as a link
    assert {s.kind_label for s in model.sources} <= set(cfg.rules.labels.source_kind.values())


def test_part_b_lists_the_full_reads_with_their_sentences(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = account()
    model = evidence_of(c, cfg, grading).model
    # the reads, newest first seen first, as the reference Part B lists them
    read = [p.posting_key for p in c.postings if p.full_text_read]
    assert sorted(j.row_id for j in model.job_descriptions) == sorted(read)
    last = model.job_descriptions[-1]
    assert last.row_id == "post-00000" and last.posted == "2026-01-01"
    assert last.first_seen == "2026-01-02" and last.platforms == "Salesforce"
    assert last.entity == c.account_name
    assert last.sentences == "Synthetic sentence naming Salesforce 0."
    assert last.posting_class == cfg.rules.labels.posting_class["admin_owner"]


# Part C (postings read in full, by lane platform) is covered in test_evidence_part_c.py


def test_part_d_one_sub_heading_per_source_read_in_depth(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    """Part D groups by source, in register order: the Summary's executive voice, then the
    sentences of full-page reads (grade and what each supports), then the listed people's own
    posts under one sub-heading, each citing its post. Every quote is verbatim."""
    c = account()
    labels = cfg.rules.documents.labels
    ev = evidence_of(c, cfg, grading)
    groups = ev.model.extracts
    assert [g.code for g in groups] == ["S-0001", "S-0005", None]
    voice_read, read, posts = groups
    # no stored title or publisher: the host names the source, never invented text
    assert voice_read.heading == f"alpha.example (30 Sep 2026), {labels.extract_voice} (S-0001)"
    assert str(voice_read.url) == "https://alpha.example/news/item-0"
    assert [(q.text, q.speaker, q.grade_label) for q in voice_read.quotes] == [
        ("Synthetic quote: we are investing in service.", "Synthetic Chief Executive", None),
        ("Synthetic sentence 0 relied on.", None, "Confirmed"),
    ]
    assert voice_read.quotes[1].supports == f"Synthetic finding 0 for {c.account_name}"
    assert read.heading == "alpha.example (30 Sep 2026) (S-0005)"
    assert [q.text for q in read.quotes] == ["Synthetic sentence 4 relied on."]
    # a search snippet is no read in depth
    assert all(q.text != "Synthetic sentence 1 relied on." for g in groups for q in g.quotes)
    assert posts.heading == labels.extract_posts and posts.url is None
    post = posts.quotes[0]
    assert (post.text, post.speaker, post.kind) == (
        "Synthetic own words from the post.",
        "Synthetic Person 000",
        labels.post_type,
    )
    assert post.code in {s.code for s in ev.model.sources}
    # no table rows: the refs re-read from the file are the other parts' only
    assert not any(r.startswith("PX-") for r in ev.refs)
    findings = (c.findings[0].model_copy(update={"person_id": "pk-nobody"}),)
    other = evidence_of(c.model_copy(update={"findings": findings}), cfg, grading).model
    assert [g.code for g in other.extracts] == ["S-0001", "S-0005"]


def test_part_d_names_a_source_by_its_stored_title(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = account()
    sources = tuple(
        s.model_copy(update={"title": "Synthetic Results Release", "published": None})
        if s.code == "S-0005"
        else s.model_copy(update={"publisher": "Synthetic Wire"})
        if s.code == "S-0001"
        else s
        for s in c.sources
    )
    groups = evidence_of(c.model_copy(update={"sources": sources}), cfg, grading).model.extracts
    voice = cfg.rules.documents.labels.extract_voice
    assert groups[0].heading == f"Synthetic Wire (30 Sep 2026), {voice} (S-0001)"
    assert groups[1].heading == "Synthetic Results Release (S-0005)"


@asyncio_test
async def test_part_d_prints_headings_links_and_quotes(
    make_builder: Make, cfg: ExportConfig
) -> None:
    text = await evidence_text(make_builder, account())
    labels = cfg.rules.documents.labels
    start = text.find(cfg.rules.headings.evidence.post_extracts)
    end = text.find(cfg.rules.headings.evidence.board_captures)
    part = text[start:end]
    assert "alpha.example (30 Sep 2026) (S-0005)" in part
    assert "https://alpha.example/news/item-4" in part
    assert "Synthetic sentence 4 relied on." in part
    assert f"{labels.speaker_prefix}: Synthetic Chief Executive" in part
    assert labels.extract_posts in part and "Synthetic own words from the post." in part


def test_parts_e_f_h_i_are_counts_leads_pulls_and_urls(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = account()
    ev = evidence_of(c, cfg, grading)
    model = ev.model
    assert [(b.keyword, b.matches, b.read) for b in model.board_captures] == [
        ("Records pulled", 5, "raw records before dedupe"),
        ("Postings naming a platform", 3, "postings analysed"),
    ]
    lead = c.facts[3]
    rule = grading.lead_rule
    tried = cfg.rules.documents.labels.lead_queries["complete"].format(
        queries=rule.min_distinct_queries, tools=rule.min_distinct_tool_categories
    )
    assert [(x.row_id, x.queries, x.grade_label, x.settle) for x in model.leads] == [
        (f"lead:{lead.id}", tried, "Lead", "Synthetic question 3?")
    ]
    assert str(rule.min_distinct_queries) in tried and "{" not in tried
    assert model.leads[0].codes == ("S-0004",)
    assert [d.pull for d in model.data_pulls][:1] == ["Detections in this file"]
    assert [(u.code, str(u.url)) for u in model.urls] == [
        (s.code, str(s.url)) for s in model.sources
    ]
    # Part I is plain lines (as the reference prints its URLs): no row keys in the file
    assert not any(r.startswith("U-") for r in ev.refs)
    assert [r for r in ev.row_ids if r.startswith("url:")] == [f"url:{u.code}" for u in model.urls]


def test_a_lead_never_re_searched_says_so(cfg: ExportConfig, grading: GradingConfig) -> None:
    c = account()
    facts = list(c.facts)
    facts[3] = facts[3].model_copy(update={"lead_status": None})
    model = evidence_of(c.model_copy(update={"facts": tuple(facts)}), cfg, grading).model
    assert model.leads[0].queries == cfg.rules.documents.labels.queries_not_tried


def _live(c: SnapshotContent, *keys: str) -> SnapshotContent:
    postings = tuple(
        p.model_copy(update={"origin": "live_board"}) if p.posting_key in keys else p
        for p in c.postings
    )
    return c.model_copy(update={"postings": postings})


def test_part_e_lists_the_live_board_postings_that_name_a_lane_platform(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    labels = cfg.rules.documents.labels
    h = cfg.rules.headings.evidence
    plain = evidence_of(account(), cfg, grading).model
    assert plain.live_postings == () and plain.live_heading == h.live_postings_undated
    c = _live(account(), "post-00000", "post-00001")
    ev = evidence_of(c, cfg, grading)
    rows = ev.model.live_postings
    # workbook order: the Jobs sheet lists the newest first seen first (post-00001, 2 February)
    assert [(r.ref, r.row_id, r.read) for r in rows] == [
        ("LV-0001", "live:post-00001", labels.read_platform_sentence),
        ("LV-0002", "live:post-00000", labels.read_in_full_yes),
    ]
    assert rows[1].posting == "Synthetic Salesforce Admin 0" and rows[1].platforms == "Salesforce"
    assert rows[1].entity == c.account_name
    assert rows[1].posting_class == cfg.rules.labels.posting_class["admin_owner"]
    assert ev.model.live_heading == h.live_postings.format(
        dates="2 January 2026 to 2 February 2026"
    )
    refs = list(ev.refs)
    assert refs.index("BC-0002") < refs.index("LV-0001") < refs.index("LD-0001")
    one = evidence_of(_live(account(), "post-00002"), cfg, grading).model
    assert one.live_heading == h.live_postings.format(dates="2 March 2026")


def test_part_h_detections_postings_entities_feed_and_teams(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = account()
    ev = evidence_of(c, cfg, grading)
    model = ev.model
    labels = cfg.rules.documents.labels
    # lane platforms only: the synthetic tools are not a lane platform
    detections = [
        (d.ref, d.technology, d.first_seen, d.last_seen, d.postings) for d in model.detections
    ]
    assert detections == [("TD-0001", "Salesforce", "2025-01", "2026-08", 3)]
    assert [
        (p.ref, p.platform, p.postings, p.open_now, p.build, p.admin_owner, p.working_tool, p.user)
        for p in model.platform_postings
    ] == [("PP-0001", "Salesforce", 3, 2, 0, 3, 0, 0)]  # a posting with no status is open
    assert [(e.ref, e.entity, e.postings) for e in model.entity_split] == [
        ("ES-0001", c.account_name, 3)
    ]
    assert model.news_feed_note == labels.news_feed_none
    assert model.teams_note == cfg.rules.none_found.teams
    refs = list(ev.refs)
    assert refs.index("DP-0001") < refs.index("TD-0001") < refs.index("PP-0001")
    assert refs.index("PP-0001") < refs.index("ES-0001") == len(refs) - 1


def test_part_h_news_feed_note_counts_the_ledger_calls(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    labels = cfg.rules.documents.labels
    at = datetime(2026, 10, 1, tzinfo=UTC)
    calls = (
        PullCall(at=at, operation="news_events", result_count=7),
        PullCall(at=at, operation="news_events", result_count=5),
        PullCall(at=at, operation="web_search", result_count=40),
    )
    c = account().model_copy(update={"pull_calls": calls})
    note = evidence_of(c, cfg, grading).model.news_feed_note
    assert note == labels.news_feed_counted.format(events=12, calls=2, feed=labels.retrieved_feed)
    uncounted = calls[:1] + (PullCall(at=at, operation="news_events"),)
    note = evidence_of(
        account().model_copy(update={"pull_calls": uncounted}), cfg, grading
    ).model.news_feed_note
    assert note == labels.news_feed_uncounted.format(calls=2, feed=labels.retrieved_feed)


@asyncio_test
async def test_parts_e_f_h_print_the_reference_sub_headings_and_columns(
    make_builder: Make, cfg: ExportConfig
) -> None:
    h = cfg.rules.headings.evidence
    text = await evidence_text(make_builder, _live(account(), "post-00000"))
    order = [
        h.board_captures,
        h.live_postings.format(dates="2 January 2026"),
        h.leads,
        h.keep_copies,
        h.data_pulls,
        h.tech_detections,
        h.platform_postings,
        h.entity_split,
        h.news_feed,
        h.teams,
        h.urls,
    ]
    positions = [text.find(x) for x in order]
    assert all(p >= 0 for p in positions) and positions == sorted(positions)
    tables = cfg.rules.documents.tables
    for name in ("live_postings", "leads", "tech_detections", "platform_postings", "entity_split"):
        assert all(header in text for header in tables[name])
    assert cfg.rules.documents.labels.news_feed_none in text
    assert cfg.rules.none_found.teams in text


@asyncio_test
async def test_parts_without_data_say_none_found(
    make_builder: Make, cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = sparse()
    model = evidence_of(c, cfg, grading).model
    assert model.job_descriptions == () and model.lane_reads == ()
    assert model.board_captures == () and model.leads == ()
    # the Summary's executive quote is the only extract of a run with nothing else read
    assert [len(g.quotes) for g in model.extracts] == [1]
    assert model.sources and model.data_pulls and model.urls and model.keep_copies
    assert model.live_postings == () and model.detections == () and model.entity_split == ()
    text = await evidence_text(make_builder, c)
    assert text.count(cfg.rules.none_found.text) == 7
    # parts E to H: the live postings, Leads, detections, postings per platform, entity split
    h = cfg.rules.headings.evidence
    e_to_h = text[text.find(h.board_captures) : text.find(h.urls)]
    assert e_to_h.count(cfg.rules.none_found.text) == 5
