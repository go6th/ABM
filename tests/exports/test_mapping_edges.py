"""Mapping edge cases: every refusal path of every mapper ends in its typed violation; news items
and tracker result counts are rendered with their bases; fingerprints of a section-less document."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest

from account_intelligence.grading.config import GradingConfig
from exports.config import ContentRules, ExportConfig, StalePhrases
from exports.content import SnapshotContent, TrackerLine
from exports.fingerprint import docx_fingerprint
from exports.policy import authored_violations
from exports.readers.common import MapContext
from exports.readers.evidence import map_evidence
from exports.readers.jobs import map_jobs
from exports.readers.people import map_people
from exports.readers.summary import map_summary
from exports.readers.tech import map_tech
from exports.readers.tracker import map_tracker
from tests.exports.corpus import account


def ctx_of(content: SnapshotContent, cfg: ExportConfig, grading: GradingConfig) -> MapContext:
    return MapContext(content, cfg, grading)


def codes(ctx: MapContext) -> set[str]:
    return {v.code for v in ctx.violations}


def capped(cfg: ExportConfig, **caps: int) -> ExportConfig:
    row_caps = cfg.rules.limits.row_caps.model_copy(update=caps)
    limits = cfg.rules.limits.model_copy(update={"row_caps": row_caps})
    return cfg.model_copy(update={"rules": cfg.rules.model_copy(update={"limits": limits})})


@pytest.mark.parametrize(
    ("mapper", "cap"),
    [
        (map_people, {"people": 1}),
        (map_jobs, {"postings": 1}),
        (map_tech, {"detections": 1}),
    ],
)
def test_every_workbook_mapper_honours_its_cap(
    mapper: Any, cap: dict[str, int], cfg: ExportConfig, grading: GradingConfig
) -> None:
    ctx = ctx_of(account(), capped(cfg, **cap), grading)
    assert mapper(ctx).model is None and codes(ctx) == {"row_cap"}


def test_summary_and_evidence_caps(cfg: ExportConfig, grading: GradingConfig) -> None:
    for cap in ({"summary_lane_rows": 1}, {"sections": 1}, {"tracker_rows": 1}):
        ctx = ctx_of(account(), capped(cfg, **cap), grading)
        map_summary(ctx, None)
        assert "row_cap" in codes(ctx), cap
    ctx = ctx_of(account(), capped(cfg, evidence_uses=1), grading)
    facts = frozenset(f.id for f in account().facts)
    assert map_evidence(ctx, set(), facts, None).model is None and codes(ctx) == {"row_cap"}


def test_people_without_any_registered_code(cfg: ExportConfig, grading: GradingConfig) -> None:
    c = account()
    people = tuple(
        p.model_copy(update={"source_code": None, "profile_url": "https://nowhere.example/x"})
        for p in c.people
    )
    ctx = ctx_of(c.model_copy(update={"people": people}), cfg, grading)
    assert map_people(ctx).model is None and codes(ctx) == {"missing_source_code"}


def test_posting_vocabularies(cfg: ExportConfig, grading: GradingConfig) -> None:
    c = account()
    ps = list(c.postings)
    ps[0] = ps[0].model_copy(update={"status": "pending"})
    ps[1] = ps[1].model_copy(update={"posting_class": "manager"})
    ctx = ctx_of(c.model_copy(update={"postings": tuple(ps)}), cfg, grading)
    assert map_jobs(ctx).model is None
    assert codes(ctx) == {"posting_status_vocabulary", "posting_class_vocabulary"}


def test_tech_without_postings_says_so(cfg: ExportConfig, grading: GradingConfig) -> None:
    ctx = ctx_of(account(n_postings=0), cfg, grading)
    model = map_tech(ctx).model
    assert model is not None
    assert {r.cross_check for r in model.rows} == {
        "No postings in the Job Postings file to cross check"
    }


def test_missing_tracker_is_refused(cfg: ExportConfig, grading: GradingConfig) -> None:
    ctx = ctx_of(account().model_copy(update={"tracker": (), "tracker_tally": ()}), cfg, grading)
    assert map_tracker(ctx) == ((), ())
    assert codes(ctx) == {"missing_tracker"}
    assert map_summary(ctx, None).model is None


def test_tracker_result_counts_are_registered_with_their_base(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = account()
    lines = list(c.tracker)
    lines[0] = TrackerLine(**{**lines[0].model_dump(), "results": 7})
    ctx = ctx_of(c.model_copy(update={"tracker": tuple(lines)}), cfg, grading)
    rows, _ = map_tracker(ctx)
    assert rows[0].results == "7" and rows[1].results == "Not counted"
    registered = {n.key: n for n in ctx.registry.items()}
    assert registered["tracker.results.000"].value == 7
    assert "results of the searches for" in registered["tracker.results.000"].base


def test_news_items_and_unknown_source_kinds(cfg: ExportConfig, grading: GradingConfig) -> None:
    rules = cfg.rules.model_copy(
        update={
            "evidence": cfg.rules.evidence.model_copy(
                update={"news_attribute_prefixes": ("tech.synthetic_0",)}
            )
        }
    )
    news_cfg = cfg.model_copy(update={"rules": rules})
    c = account()
    ctx = ctx_of(c, news_cfg, grading)
    facts = frozenset(f.id for f in c.facts)
    model = map_evidence(ctx, set(), facts, None).model
    assert model is not None and len(model.news) == 1 and model.news[0].verified  # full read
    sources = list(c.sources)
    sources[0] = sources[0].model_copy(update={"kind": "rumour"})
    ctx = ctx_of(c.model_copy(update={"sources": tuple(sources)}), cfg, grading)
    map_evidence(ctx, set(), facts, None)
    assert "source_kind_vocabulary" in codes(ctx)


def test_evidence_of_a_fact_not_rendered_is_not_listed(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = account()
    ctx = ctx_of(c, cfg, grading)
    model = map_evidence(ctx, set(), frozenset(), None).model
    assert model is not None and model.sources == ()


def test_absence_facts_render_with_their_question(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = account()
    facts = list(c.facts)
    facts[2] = facts[2].model_copy(update={"claim_kind": "absence"})
    ev = tuple(
        e.model_copy(update={"evidence_kind": "search_proof", "url": None})
        if e.fact_id == facts[2].id
        else e
        for e in c.evidence
    )
    ctx = ctx_of(c.model_copy(update={"facts": tuple(facts), "evidence": ev}), cfg, grading)
    model = map_summary(ctx, map_people(ctx).model).model
    assert model is not None and [a.row_id for a in model.absences] == [facts[2].id]
    assert facts[2].id not in {r.row_id for t in model.lanes for r in t.rows}


def test_a_non_matching_stale_phrase_passes(rules: ContentRules) -> None:
    r = rules.model_copy(
        update={"stale_phrases": StalePhrases(source="t", phrases=("as of today", "now"))}
    )
    assert authored_violations("Plain text.", r, "w", is_partner=False) == ()


def test_fingerprint_of_a_document_without_a_section() -> None:
    body = io.BytesIO()
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    with zipfile.ZipFile(body, "w") as z:
        z.writestr("word/document.xml", f"<w:document {w}><w:body><w:p/></w:body></w:document>")
        z.writestr("word/styles.xml", f"<w:styles {w}/>")
    fp = docx_fingerprint(body.getvalue())
    assert fp["page"] == {} and fp["rPr"] == []
