"""p_signals: company news events, job openings and technology detections (JSON:API).

Spec: docs/providers/p_signals.md. Feeds are pulled once per run: the arguments are
only the domain and an optional delta date, the page size is fixed by config, so the
same feed always maps to the same ledger keys and a second request replays.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from account_intelligence.connectors.base import (
    Operation,
    PageRequest,
    ParsedPage,
    ProviderCode,
    RawResponse,
)
from account_intelligence.connectors.errors import NotSupportedYet
from account_intelligence.connectors.models import (
    FeedArgs,
    JobOpeningRecord,
    NewsEventRecord,
    OperationArgs,
    Record,
    TechDetectionRecord,
)
from account_intelligence.connectors.providers._common import (
    Strict,
    classify_status,
    decode_json,
    provenance,
    validate,
)

CODE = ProviderCode.P_SIGNALS

_PATHS = {
    Operation.NEWS_EVENTS: "news_events",
    Operation.JOB_OPENINGS: "job_openings",
    Operation.TECH_DETECTIONS: "technology_detections",
}
# Delta pulls ("found from last pull"): the documented date filter per feed.
_SINCE_PARAM = {
    Operation.NEWS_EVENTS: "found_at_from",
    Operation.JOB_OPENINGS: "first_seen_at_from",
    Operation.TECH_DETECTIONS: "first_seen_at_from",
}


class _Ref(Strict):
    id: str
    type: str


class _RefOne(Strict):
    data: _Ref | None


class _RefMany(Strict):
    data: list[_Ref]


class _Meta(Strict):
    count: int | None = None  # only present when `page` is sent (it always is here)


class _NewsAttrs(Strict):
    summary: str
    category: str
    found_at: str
    confidence: float
    article_sentence: str | None
    planning: bool
    amount_normalized: int | None
    effective_date: str | None


class _NewsRels(Strict):
    most_relevant_source: _RefOne


class _NewsItem(Strict):
    id: str
    attributes: _NewsAttrs
    relationships: _NewsRels


class _JobAttrs(Strict):
    title: str
    normalized_title: str | None
    description: str | None
    url: str
    first_seen_at: str
    last_seen_at: str
    posted_at: str | None
    status: str | None
    seniority: str | None
    location: str | None
    categories: list[str]


class _JobItem(Strict):
    id: str
    attributes: _JobAttrs


class _TechAttrs(Strict):
    first_seen_at: str
    last_seen_at: str
    behind_firewall: bool
    score: float
    source_count: int


class _TechRels(Strict):
    technology: _RefOne
    seen_on_subpages: _RefMany
    seen_on_job_openings: _RefMany


class _TechItem(Strict):
    id: str
    attributes: _TechAttrs
    relationships: _TechRels


class _Included(Strict):
    id: str
    type: str
    attributes: dict[str, Any]


class _Envelope(Strict):
    data: list[dict[str, Any]]
    included: list[_Included]
    meta: _Meta | None = None


class SignalsConnector:
    """The p_signals adapter: news, technology and job feeds, pulled once per run and paged."""

    code = CODE

    def __init__(self, page_limit: int) -> None:
        if not 1 <= page_limit <= 1000:  # OpenAPI: limit maximum 1000
            raise ValueError("page_limit must be within the documented 1..1000")
        self.page_limit = page_limit

    def supports(self, op: Operation) -> bool:
        """The three company feeds."""
        return op in _PATHS

    def build_request(self, op: Operation, args: OperationArgs, cursor: str | None) -> PageRequest:
        """One feed page for the account's domain (since makes a delta pull)."""
        if op not in _PATHS:
            raise NotSupportedYet(provider_code=CODE, operation=op)
        assert isinstance(args, FeedArgs)
        page = int(cursor) if cursor is not None else 1
        params: dict[str, str | int | bool] = {"page": page, "limit": self.page_limit}
        if args.since is not None:
            params[_SINCE_PARAM[op]] = args.since.isoformat()
        path = f"/companies/{quote(args.domain, safe='.-')}/{_PATHS[op]}"
        return PageRequest("GET", path, params=params)

    def poll_request(self, op: Operation, job_id: str) -> PageRequest:
        """No asynchronous jobs."""
        raise NotSupportedYet("no async jobs", provider_code=CODE, operation=op)

    def classify_error(self, op: Operation, raw: RawResponse) -> Exception:
        """Map statuses to typed errors (403 and 429 are rate limits here)."""
        # OpenAPI responses: 401 credentials or subscription, 402 monthly request limit,
        # 403 "Request rate over limit", 404 record not found, 422, 429 (Retry-After
        # seconds), 500, 503.
        # VERIFY(p_signals): numeric rate limit is not published (config placeholder).
        return classify_status(raw, CODE, op, rate_limited=frozenset({403, 429}))

    def parse(
        self,
        op: Operation,
        args: OperationArgs,
        raw: RawResponse,
        base: Mapping[str, Any],
        cursor: str | None,
    ) -> ParsedPage:
        """Validate the envelope and normalise one feed page into records."""
        envelope = validate(_Envelope, decode_json(raw, CODE, op), CODE, op)
        page = int(cursor) if cursor is not None else 1
        included = {(i.type, i.id): i.attributes for i in envelope.included}
        records: list[Record] = []
        for index, item in enumerate(envelope.data):
            prov_kw = {"page": page, "index": index}
            if op is Operation.NEWS_EVENTS:
                news = validate(_NewsItem, item, CODE, op)
                ref = news.relationships.most_relevant_source.data
                article = included.get((ref.type, ref.id)) if ref is not None else None
                article_url = _str_or_none(article, "url")
                records.append(
                    NewsEventRecord(
                        kind="news_event",
                        provenance=provenance(base, source_url=article_url, **prov_kw),
                        lineage_hint=article_url,  # the provider states the article it read
                        event_id=news.id,
                        category=news.attributes.category,
                        summary_text=news.attributes.summary,
                        found_at=news.attributes.found_at,
                        confidence=news.attributes.confidence,
                        article_sentence_text=news.attributes.article_sentence,
                        effective_date=news.attributes.effective_date,
                        amount_normalized_usd=news.attributes.amount_normalized,
                        planning=news.attributes.planning,
                        article_url=article_url,
                        article_title_text=_str_or_none(article, "title"),
                        article_published_at=_str_or_none(article, "published_at"),
                    )
                )
            elif op is Operation.JOB_OPENINGS:
                job = validate(_JobItem, item, CODE, op)
                a = job.attributes
                records.append(
                    JobOpeningRecord(
                        kind="job_opening",
                        provenance=provenance(base, source_url=a.url, **prov_kw),
                        lineage_hint=None,
                        job_id=job.id,
                        title_text=a.title,
                        normalized_title=a.normalized_title,
                        description_text=a.description,
                        url=a.url,
                        first_seen_at=a.first_seen_at,
                        last_seen_at=a.last_seen_at,
                        posted_at=a.posted_at,
                        status=a.status,
                        seniority=a.seniority,
                        location=a.location,
                        categories=tuple(a.categories),
                    )
                )
            else:
                tech = validate(_TechItem, item, CODE, op)
                tref = tech.relationships.technology.data
                tech_attrs = included.get((tref.type, tref.id)) if tref is not None else None
                evidence: list[str] = []
                for r in tech.relationships.seen_on_subpages.data:
                    url = _str_or_none(included.get((r.type, r.id)), "subpage_url")
                    if url is not None:
                        evidence.append(url)
                for r in tech.relationships.seen_on_job_openings.data:
                    url = _str_or_none(included.get((r.type, r.id)), "url")
                    if url is not None:
                        evidence.append(url)
                records.append(
                    TechDetectionRecord(
                        kind="tech_detection",
                        provenance=provenance(
                            base, source_url=evidence[0] if evidence else None, **prov_kw
                        ),
                        lineage_hint=None,
                        detection_id=tech.id,
                        technology_id=tref.id if tref is not None else None,
                        technology_name=_str_or_none(tech_attrs, "name"),
                        first_seen_at=tech.attributes.first_seen_at,
                        last_seen_at=tech.attributes.last_seen_at,
                        score=tech.attributes.score,
                        source_count=tech.attributes.source_count,
                        behind_firewall=tech.attributes.behind_firewall,
                        evidence_urls=tuple(evidence),
                    )
                )
        count = envelope.meta.count if envelope.meta is not None else None
        if count is not None:
            more = page * self.page_limit < count
        else:  # count is documented as present whenever `page` is sent; fall back safely
            more = len(envelope.data) >= self.page_limit
        return ParsedPage(
            records=tuple(records),
            items_read=len(envelope.data),
            next_cursor=str(page + 1) if more and envelope.data else None,
        )


def _str_or_none(attrs: Mapping[str, Any] | None, key: str) -> str | None:
    if attrs is None:
        return None
    value = attrs.get(key)
    return value if isinstance(value, str) else None
