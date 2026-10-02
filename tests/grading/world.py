"""A synthetic run for grading tests (labelled synthetic; no real company or person).

Sources cover every origin: a live careers-page read (T1, full page), a cached leadership
page read (T1 host, cached copy), a news feed item with a stated upstream, a search snippet
from an unlisted domain, and a broker record. The ledger includes a failed call and a call
from a previous run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid5

from account_intelligence.grading.ports import FetchRecord, RunInfo, RunSource
from account_intelligence.urls import normalize_url
from tests.grading.conftest import NOW

NS = UUID("00000000-0000-0000-0000-00000000a11c")
TENANT = uuid5(NS, "tenant-synthetic")
ACCOUNT = uuid5(NS, "account-synthetic")
RUN = uuid5(NS, "run-synthetic")
PREV_RUN = uuid5(NS, "run-previous")
RUN_START = NOW - timedelta(hours=2)

CAREERS = "https://careers.acme.example/jobs/42"
LEADERSHIP = "https://acme.example/leadership"
NEWS = "https://news.example.org/acme-raises-series-b"
SNIPPET = "https://blog.example.net/acme-cto"
BROKER = "https://people.example/p/123"
OLD_PAGE = "https://acme.example/about"

CAREERS_TEXT = (
    "Acme is hiring a Senior Salesforce Administrator to own the CRM platform. "
    "The team uses Salesforce Sales Cloud and Service Cloud. Apply by October 30."
)
LEADERSHIP_TEXT = (
    "Our leadership. Jane Doe is the Chief Technology Officer of Acme. She joined in 2024."
)
NEWS_TEXT = "Acme raised $40 million in Series B funding led by Example Ventures."
SNIPPET_TEXT = (
    "Industry blog: Jane Doe is the Chief Technology Officer of Acme, according to the company."
)
BROKER_TEXT = "Jane Doe, Chief Technology Officer at Acme, based in Pune."
OLD_TEXT = "Acme was founded in 2019 by Jane Doe and John Roe."

RUN_INFO = RunInfo(
    TENANT, ACCOUNT, RUN, RUN_START, "acme.example", "Acme", frozenset({"careers.acme.example"})
)


def _source(
    ref: str, call: str, url: str, text: str, origin: str, provider: str, op: str, **kw: Any
) -> RunSource:
    return RunSource(
        source_ref=f"tenants/t/accounts/a/runs/r/raw/{ref}.json.gz#0",
        call_ref=call,
        run_id=kw.pop("run_id", RUN),
        url=url,
        url_normalized=normalize_url(url),
        host=url.split("/")[2],
        text=text,
        origin=origin,  # type: ignore[arg-type]
        provider_code=provider,
        operation=op,
        fetched_at=kw.pop("fetched_at", NOW - timedelta(hours=1)),
        **kw,
    )


def _fetch(call: str, op: str, origin: str, cat: str, provider: str, **kw: Any) -> FetchRecord:
    return FetchRecord(
        call_ref=call,
        run_id=kw.pop("run_id", RUN),
        provider_code=provider,
        operation=op,
        origin=origin,  # type: ignore[arg-type]
        tool_category=cat,
        fetched_at=kw.pop("fetched_at", NOW - timedelta(hours=1)),
        success=kw.pop("success", True),
        **kw,
    )


@dataclass
class World:
    sources: list[RunSource] = field(default_factory=list)
    fetches: dict[str, FetchRecord] = field(default_factory=dict)


def world() -> World:
    w = World()
    w.sources = [
        _source("c1", "c1", CAREERS, CAREERS_TEXT, "full_page_read", "p_scrape", "scrape_job_page"),
        _source("c2", "c2", LEADERSHIP, LEADERSHIP_TEXT, "cached_copy", "p_search", "read_page"),
        _source(
            "c3",
            "c3",
            NEWS,
            NEWS_TEXT,
            "feed_item",
            "p_signals",
            "news_events",
            published_at=NOW - timedelta(days=30),
            lineage_hint=NEWS,
        ),
        _source("c4", "c4", SNIPPET, SNIPPET_TEXT, "search_snippet", "p_search", "web_search"),
        _source("c5", "c5", BROKER, BROKER_TEXT, "broker_record", "p_org", "people_by_technology"),
        _source(
            "c9",
            "c9",
            OLD_PAGE,
            OLD_TEXT,
            "full_page_read",
            "p_scrape",
            "scrape_job_page",
            run_id=PREV_RUN,
            fetched_at=NOW - timedelta(days=40),
        ),
    ]
    w.fetches = {
        "c1": _fetch("c1", "scrape_job_page", "full_page_read", "job_board", "p_scrape"),
        "c2": _fetch("c2", "read_page", "cached_copy", "page_read", "p_search"),
        "c3": _fetch(
            "c3",
            "news_events",
            "feed_item",
            "news_feed",
            "p_signals",
            queries=("news_events domain=acme.example",),
        ),
        "c4": _fetch(
            "c4",
            "web_search",
            "search_snippet",
            "web_search",
            "p_search",
            queries=("acme chief technology officer", "Acme CTO name"),
        ),
        "c5": _fetch(
            "c5",
            "people_by_technology",
            "broker_record",
            "people_search",
            "p_org",
            queries=("people_by_technology org=1 tech=salesforce",),
        ),
        "c6": _fetch(
            "c6",
            "web_search",
            "search_snippet",
            "web_search",
            "p_search",
            queries=("acme salesforce administrator job posting", "Salesforce administrator Acme"),
        ),
        "c7": _fetch(
            "c7",
            "web_search",
            "search_snippet",
            "web_search",
            "p_search",
            queries=("acme crm platform team hiring",),
            success=False,
        ),
        "c8": _fetch(
            "c8",
            "job_openings",
            "feed_item",
            "job_board",
            "p_signals",
            queries=("job_openings domain=acme.example",),
        ),
        "c9": _fetch(
            "c9",
            "scrape_job_page",
            "full_page_read",
            "job_board",
            "p_scrape",
            run_id=PREV_RUN,
            fetched_at=NOW - timedelta(days=40),
        ),
    }
    return w


def sourced(local: str, key: str, value: str, *ev: tuple[str, str], **extra: Any) -> dict[str, Any]:
    return {
        "claim_kind": "sourced",
        "local_id": local,
        "attribute_key": key,
        "value": value,
        "evidence": [{"url": u, "supporting_sentence": s} for u, s in ev],
        **extra,
    }
