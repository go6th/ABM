"""Adapter text extraction for every record kind, date parsing, queries; docs CLI paths."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.models import (
    JobOpeningRecord,
    NewsEventRecord,
    OrganizationRecord,
    PersonRecord,
    Provenance,
    ScrapedPageRecord,
    TechDetectionRecord,
    TechStackRecord,
    WebPageRecord,
)
from account_intelligence.grading import docs
from account_intelligence.grading.adapters import call_queries, record_published, record_text

P = Provenance(
    provider_code=ProviderCode.P_SIGNALS,
    operation=Operation.NEWS_EVENTS,
    source_url="https://x.example/a",
    retrieved_at=datetime(2026, 10, 1, tzinfo=UTC),
    raw_ref="r",
    page=1,
    index=0,
)
BASE = {"provenance": P, "lineage_hint": None}


def test_record_text_for_every_kind() -> None:
    cases = [
        (
            WebPageRecord(
                kind="web_page",
                url="u",
                title_text="T",
                publish_date="2026-09-01T00:00:00Z",
                excerpts_text=("e1", "e2"),
                full_content_text=None,
                **BASE,
            ),
            "T\ne1\ne2",
        ),
        (
            ScrapedPageRecord(
                kind="scraped_page",
                requested_url=None,
                final_url=None,
                target_status=200,
                markdown_text="M",
                title_text=None,
                publish_date=None,
                **BASE,
            ),
            "M",
        ),
        (
            NewsEventRecord(
                kind="news_event",
                event_id="e",
                category="c",
                summary_text="S",
                found_at="2026-09-01T00:00:00Z",
                confidence=0.5,
                article_sentence_text="A",
                effective_date=None,
                amount_normalized_usd=None,
                planning=False,
                article_url=None,
                article_title_text="H",
                article_published_at=None,
                **BASE,
            ),
            "H\nS\nA",
        ),
        (
            JobOpeningRecord(
                kind="job_opening",
                job_id="j",
                title_text="J",
                normalized_title=None,
                description_text="D",
                url="u",
                first_seen_at="2026-09-01",
                last_seen_at="x",
                posted_at=None,
                status=None,
                seniority=None,
                location=None,
                categories=(),
                **BASE,
            ),
            "J\nD",
        ),
        (
            TechDetectionRecord(
                kind="tech_detection",
                detection_id="d",
                technology_id=None,
                technology_name="Py",
                first_seen_at="a",
                last_seen_at="b",
                score=1.0,
                source_count=1,
                behind_firewall=False,
                evidence_urls=(),
                **BASE,
            ),
            "Py",
        ),
        (
            OrganizationRecord(
                kind="organization",
                input_domain="a.example",
                matched=True,
                organization_id=1,
                slug=None,
                name="N",
                url=None,
                employee_count=None,
                industry="I",
                headquarters_country=None,
                **BASE,
            ),
            "N\nI",
        ),
        (
            TechStackRecord(
                kind="tech_stack_item",
                organization_id=1,
                business_function="B",
                category_slug="c",
                category_name="C",
                technology_name="T",
                technology_slug="t",
                job_post_used_count=1,
                **BASE,
            ),
            "T\nC\nB",
        ),
        (
            PersonRecord(
                kind="person",
                provider_person_id="1",
                profile_url=None,
                name_text="Jane",
                job_title_text="CTO",
                job_function=None,
                job_level=None,
                location=None,
                **BASE,
            ),
            "Jane\nCTO",
        ),
    ]
    for record, expected in cases:
        assert record_text(record) == expected
    assert record_published(cases[0][0]) == datetime(2026, 9, 1, tzinfo=UTC)
    assert record_published(cases[2][0]) == datetime(
        2026, 9, 1, tzinfo=UTC
    )  # falls back to found_at
    # a calendar date alone is a dateline (the feed's first-seen day): midnight UTC
    assert record_published(cases[3][0]) == datetime(2026, 9, 1, tzinfo=UTC)
    no_zone = cases[0][0].model_copy(update={"publish_date": "2026-09-01T10:00:00"})
    assert record_published(no_zone) is None  # a time without a zone is not trusted
    assert record_published(cases[1][0]) is None
    bad = cases[0][0].model_copy(update={"publish_date": "not a date"})
    assert record_published(bad) is None


def test_call_queries() -> None:
    assert call_queries("web_search", {"queries": ["a b", "c d"]}) == ("a b", "c d")
    assert call_queries("news_events", {"domain": "x.example", "since": None}) == (
        'news_events {"domain":"x.example","since":null}',
    )


def test_docs_cli_writes_and_detects_staleness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(docs, "OUT", tmp_path / "docs" / "grading")
    monkeypatch.setattr(docs, "ROOT", tmp_path)
    assert docs.main(["--check"]) == 1 and "stale" in capsys.readouterr().out
    assert docs.main([]) == 0
    assert docs.main(["--check"]) == 0
