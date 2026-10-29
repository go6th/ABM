"""The Account Summary follows the owner's section order (master prompt v2 section 9.1): title
block, legend, plain words, news, baseline, signals, lanes, people, hypotheses, questions, the
coverage tracker and source links. The tiered tables are views of the lane rows; every empty
section says "None found"; the tracker is printed pass by pass after the consistency rules."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest

from account_intelligence.grading.config import GradingConfig
from account_intelligence.tracker.model import Status
from account_intelligence.tracker.render import STATUS_TEXT
from exports.builder import ExportBuilder, map_snapshot
from exports.config import ExportConfig
from exports.content import SnapshotContent
from exports.verify import docx_text
from tests.exports.corpus import account, outage, sparse, tracker
from tests.fakes.exports import MemoryStore

asyncio_test = pytest.mark.asyncio
Make = Callable[..., ExportBuilder]

# the heading keys in the order the sections are rendered (sub-headings included)
ORDER = (
    "how_to_read",
    "narrative",
    "caveats",
    "decision_changes",
    "executive_voice",
    "news",
    "baseline",
    "signals",
    "signals_tier1",
    "signals_tier2",
    "signals_tier3",
    "signals_context",
    "lane_tables",
    "people",
    "people_method",
    "key_people",
    "linkedin_findings",
    "status_changed",
    "people_interpretation",
    "hypotheses",
    "sensitive_cautions",
    "first_call_questions",
    "coverage_tracker",
    "tracker_waiting",
    "tracker_pass1",
    "tracker_pass2",
    "source_links",
)


async def summary_text(make: Make, content: SnapshotContent) -> str:
    store = MemoryStore()
    result = await make(content, store).build_all(content.tenant_id, content.run_id)
    assert result.status == "complete", result.errors
    d = next(x for x in result.deliverables if x.kind == "summary")
    return docx_text(store.objects[d.object_key])


def tiered(**dates: str | None) -> SnapshotContent:
    """The base account with its facts renamed into the attribute vocabulary: fact 0 and 4 are
    tier 1 and dated (news), fact 1 is tier 0 (baseline), fact 2 is tier 2 and dated before the
    news window (context and history), fact 3 is tier 3 and undated."""
    c = account()
    plan = {
        0: ("platform_selection", "2026-06-01"),
        1: ("headquarters", None),
        2: ("acquisition", "2024-01-15"),
        3: ("executive_role", None),
        4: ("vendor_partnership", "2026-08-15"),
    }
    facts = list(c.facts)
    for i, (attribute, when) in plan.items():
        facts[i] = facts[i].model_copy(
            update={"attribute_key": attribute, "event_date": dates.get(attribute, when)}
        )
    return c.model_copy(update={"facts": tuple(facts)})


@asyncio_test
async def test_sections_are_in_the_specification_order(
    make_builder: Make, cfg: ExportConfig
) -> None:
    text = await summary_text(make_builder, account())
    headings = cfg.rules.headings.summary
    # each heading is searched for after the previous one: short headings such as People also
    # occur inside file names and sentences earlier in the file
    at, positions = 0, {}
    for key in ORDER:
        at = text.find(getattr(headings, key), at)
        positions[key] = at
        assert at >= 0, positions
    for banned in ("—", "–"):
        assert banned not in text


@asyncio_test
async def test_title_block_names_the_set(make_builder: Make, cfg: ExportConfig) -> None:
    # the reference opening: research date, the other four files, the attribution; no version
    # or domain line (the run version stays in the manifest and the model data)
    c = account().model_copy(update={"domain": "alpha.example"})
    text = await summary_text(make_builder, c)
    labels = cfg.rules.documents.labels
    opening = text.split(cfg.rules.headings.summary.how_to_read, 1)[0]
    assert "Research date " in opening and f"{labels.research_by}." in opening
    assert f"{labels.version_prefix}: " not in opening and "alpha.example" not in opening
    for name in (
        "Sources_and_Evidence.docx",
        "People_List.xlsx",
        "Job_Postings.xlsx",
        "Tech_Stack",
    ):
        assert name in opening
    assert "Account_Summary" not in opening


def test_the_legend_is_the_skill_wording_under_the_grading_policy(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    mapped, ctx = map_snapshot(account(), cfg, grading)
    model = mapped["summary"].model
    assert model is not None
    legend = {g.grade: (g.meaning, g.use) for g in model.grade_legend}
    # the skill's output specification, word for word (output-spec.md "Grades")
    assert legend == {
        "Confirmed": ("Read in a primary source during this run", "May be stated"),
        "Probable": (
            "Inferred from one or two sources, reasoning given in the row",
            "Ask, do not state",
        ),
        "Hypothesis": ("Synthesis, no source states it", "Ask, do not state"),
        "Lead": ("Seen in a secondary source, not verified", "Do not use until checked"),
    }
    assert not ctx.violations


def test_the_legend_never_lets_a_forbidden_grade_be_stated(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    labels = cfg.rules.documents.labels
    text = cfg.rules.statement_text
    worded = dict(labels.grade_legend)
    worded["lead"] = worded["lead"].model_copy(update={"use": text.may_state})
    del worded["probable"]
    rules = cfg.rules.model_copy(
        update={
            "documents": cfg.rules.documents.model_copy(
                update={"labels": labels.model_copy(update={"grade_legend": worded})}
            )
        }
    )
    mapped, _ = map_snapshot(account(), cfg.model_copy(update={"rules": rules}), grading)
    legend = {g.grade: (g.meaning, g.use) for g in mapped["summary"].model.grade_legend}
    # a grade the policy forbids stating falls back to the ask text; an unworded grade takes
    # the grading configuration's meaning
    assert legend["Lead"][1] == text.ask
    assert legend["Probable"] == (grading.meanings["probable"], text.ask)


@asyncio_test
async def test_how_to_read_opens_with_the_source_id_line(
    make_builder: Make, cfg: ExportConfig
) -> None:
    text = await summary_text(make_builder, account())
    h = cfg.rules.headings.summary
    intro = cfg.rules.documents.labels.legend_intro
    assert text.find(h.how_to_read) < text.find(intro) < text.find("Use in outreach")


def test_news_baseline_and_signals_are_views_of_the_lane_rows(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    content = tiered()
    mapped, ctx = map_snapshot(content, cfg, grading)
    model = mapped["summary"].model
    assert model is not None and not ctx.violations
    by_fact = {r.row_id: r for t in model.lanes for r in t.rows}
    # news: the two dated tier 1 facts inside the 18 months, newest first, read as their evidence,
    # dated as the reference prints dates
    assert [(n.ref, n.date, n.read) for n in model.news] == [
        ("RN-0001", "15 Aug 2026", "Read"),
        ("RN-0002", "1 Jun 2026", "Read"),
    ]
    tier1 = cfg.rules.headings.summary.signals_tier1
    lane = next(t.lane for t in model.lanes for r in t.rows if r.row_id == content.facts[4].id)
    assert model.news[0].why == f"{tier1} ({lane})"
    assert model.news[0].codes == by_fact[content.facts[4].id].codes
    assert model.news_sentence == cfg.rules.documents.labels.news_sentence.format(n=2, months=18)
    # baseline: the tier 0 fact with its item and the lane row's grade, no visible key
    row = by_fact[content.facts[1].id]
    assert [(b.item, b.grade_label, b.may_state) for b in model.baseline] == [
        ("Headquarters", row.grade_label, row.may_state)
    ]
    # signals: tier 1 holds the two news facts, tier 3 the undated one, context the old one
    tables = {t.key: [(s.ref, s.date) for s in t.rows] for t in model.signals}
    assert [t.key for t in model.signals] == ["tier1", "tier2", "tier3", "context"]
    assert tables["tier1"] == [("SG-0001", "1 Jun 2026"), ("SG-0002", "15 Aug 2026")]
    assert tables["tier2"] == []
    assert tables["tier3"] == [("SG-0003", cfg.rules.documents.labels.not_dated)]
    assert tables["context"] == [("SG-0004", "15 Jan 2024")]
    # the refs of the file lead with the tiered views, in document order
    assert list(mapped["summary"].refs[:6]) == [
        "RN-0001",
        "RN-0002",
        "SG-0001",
        "SG-0002",
        "SG-0003",
        "SG-0004",
    ]
    registered = {n.key: n.value for n in ctx.registry.items()}
    assert registered["summary.news"] == 2


def test_dates_print_at_the_precision_they_were_stored(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    content = tiered(platform_selection="2026-06", vendor_partnership="2026")
    mapped, _ = map_snapshot(content, cfg, grading)
    model = mapped["summary"].model
    assert model is not None
    # a month alone never becomes the first of the month, a year alone stays a year
    assert [n.date for n in model.news] == ["Jun 2026", "2026"]
    assert [s.date for s in model.signals[0].rows] == ["Jun 2026", "2026"]


@asyncio_test
async def test_a_tiered_account_builds_and_prints_the_timeline(make_builder: Make) -> None:
    content = tiered()
    text = await summary_text(make_builder, content)
    assert "The timeline covers 2 dated findings from the last 18 months" in text
    lane = re.search(r"(?<![A-Z])L-0001", text).start()
    # the baseline is a bullet "Item: what (sources)." between the news and the signals
    baseline = text.find("Headquarters: ")
    assert text.find("RN-0001") < baseline < text.find("SG-0001") < lane
    assert "15 Aug 2026" in text


@asyncio_test
async def test_every_empty_section_says_none_found(make_builder: Make, cfg: ExportConfig) -> None:
    text = await summary_text(make_builder, sparse())
    # news, baseline, four signal tables, lane tables, how the list was built, key people,
    # findings from profiles, status changed and the people interpretation, and section 1's
    # what stands out and lane meanings (not in the corpus): fourteen empty sections, each
    # with the note
    assert text.count(cfg.rules.none_found.text) == 14
    assert "The timeline covers 0 dated findings" in text


def test_tracker_is_printed_pass_by_pass_with_waiting_rows_first(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    mapped, ctx = map_snapshot(outage(), cfg, grading)
    model = mapped["summary"].model
    assert model is not None and not ctx.violations
    rows = model.tracker
    assert [t.pass_no for t in rows] == [1] * 5 + [2] * 28
    assert [t.ref for t in rows] == [f"T-{i:04d}" for i in range(1, 34)]
    # within a pass: High first, then Medium, then Optional
    rank = {"High": 0, "Medium": 1, "Optional": 2}
    for pass_no in (1, 2):
        ranks = [rank[t.priority] for t in rows if t.pass_no == pass_no]
        assert ranks == sorted(ranks)
    # within High pass 2 rows, the rows that need nothing come before the rows waiting on someone
    high = [t for t in rows if t.pass_no == 2 and t.priority == "High"]
    needs = [t.needs != cfg.rules.documents.labels.needs_nothing for t in high]
    assert needs == sorted(needs)
    blocked = {t.row_id for t in rows if t.status == STATUS_TEXT["blocked"]}
    assert blocked == {"job_board_by_lane", "job_postings_12_months", "customers_partners_matched"}
    assert {w.row_id for w in model.waiting} == {f"waiting:{k}" for k in blocked}
    assert all(w.needs for w in model.waiting)
    remains = cfg.rules.documents.labels.remains
    assert {t.remains for t in rows} <= set(remains.values())
    assert any(" 3 Blocked," in s for s in model.tracker_tally)  # the tally follows the rules
    assert model.tally_line is not None and "Blocked 3" in model.tally_line


def test_the_consistency_rules_read_the_facts(cfg: ExportConfig, grading: GradingConfig) -> None:
    lines, tally = tracker({"company_news": Status.NONE, "leadership_changes": Status.NONE})
    c = tiered().model_copy(update={"tracker": lines, "tracker_tally": tally})
    facts = list(c.facts)
    facts[4] = facts[4].model_copy(
        update={"attribute_key": "appointment", "event_date": "2026-08-15"}
    )
    c = c.model_copy(update={"facts": tuple(facts)})
    mapped, _ = map_snapshot(c, cfg, grading)
    by_key = {t.row_id: t for t in mapped["summary"].model.tracker}
    assert by_key["company_news"].status == STATUS_TEXT["partial"]
    assert by_key["leadership_changes"].status == STATUS_TEXT["partial"]
    # without dated facts the None rows stand
    plain = account().model_copy(update={"tracker": lines, "tracker_tally": tally})
    mapped, _ = map_snapshot(plain, cfg, grading)
    by_key = {t.row_id: t for t in mapped["summary"].model.tracker}
    assert by_key["company_news"].status == STATUS_TEXT["none"]
    assert by_key["leadership_changes"].status == STATUS_TEXT["none"]


def test_source_links_list_every_code_the_file_cites(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    mapped, _ = map_snapshot(account(), cfg, grading)
    summary = mapped["summary"]
    model = summary.model
    assert model is not None
    assert [s.code for s in model.source_links] == sorted(summary.cited, key=lambda c: int(c[2:]))
    assert all(s.domain == "alpha.example" for s in model.source_links)
    assert list(summary.refs[-len(model.source_links) :]) == [s.ref for s in model.source_links]


@asyncio_test
async def test_the_sensitive_sentence_is_a_caution_under_the_hypotheses(
    make_builder: Make, cfg: ExportConfig
) -> None:
    text = await summary_text(make_builder, account())
    h = cfg.rules.headings.summary
    # a bullet under "Cautions for sensitive findings", before the first-call questions; the
    # hypotheses are a Word numbered list (no typed "1. ")
    caution = cfg.rules.sensitive_sentence.text
    at = text.find(h.hypotheses)
    assert at < text.find(h.sensitive_cautions, at) < text.find(caution, at)
    assert text.find(caution, at) < text.find(h.first_call_questions, at)
    assert "Synthetic hypothesis" in text and "1. Synthetic hypothesis" not in text


def test_people_intro_comes_from_the_people_counts(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    mapped, _ = map_snapshot(account(), cfg, grading)
    model: Any = mapped["summary"].model
    assert model.people_intro[0] == "4 people in scope of 5 distinct people found"
    assert "1 excluded security function" in model.people_intro
    sparse_model: Any = map_snapshot(sparse(), cfg, grading)[0]["summary"].model
    assert sparse_model.people_intro == ()
