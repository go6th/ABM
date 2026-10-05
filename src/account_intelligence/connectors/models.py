"""Typed operation arguments and normalised records.

Rules enforced here (prompt sections 2.3 to 2.7):
* Argument models forbid extra fields, carry no tenant id, validate domains with the
  accounts CHECK pattern, and validate URL shape (network checks are in security.py).
* Records cannot be built without provenance; absent provider fields are ``None``,
  never ``""``, ``0`` or ``[]``. Fields holding provider or page text end in ``_text``
  and are untrusted: they are stored and passed on as data, never interpolated.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from account_intelligence.connectors.base import Operation, ProviderCode

# Same rule as ck_accounts_domain_shape (migrations/0003_accounts_extension.sql).
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _domain(value: str) -> str:
    if value != value.lower() or not DOMAIN_RE.fullmatch(value) or value.startswith("www."):
        raise ValueError("domain must be a bare lowercase hostname without www.")
    if len(value) > 253:
        raise ValueError("domain too long")
    return value


def _clean_text(value: str) -> str:
    if _CONTROL.search(value):
        raise ValueError("control characters are not allowed")
    return value


def _https_url_shape(value: str) -> str:
    # Shape only; DNS, private ranges and the allow-list are checked by security.py
    # at call time because they need the call context and a resolver.
    from account_intelligence.connectors.security import check_url_shape

    check_url_shape(value)
    return value


Domain = Annotated[str, AfterValidator(_domain)]
QueryText = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_clean_text)]
HttpsUrl = Annotated[str, Field(max_length=2048), AfterValidator(_https_url_shape)]
TechSlug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.+-]{0,99}$")]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class WebSearchArgs(_Args):
    """p_search POST /v1/search. ``queries`` maps to ``search_queries`` (3 to 6 words each)."""

    objective: (
        Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(_clean_text)] | None
    ) = None
    queries: tuple[QueryText, ...] = Field(min_length=1, max_length=5)
    mode: Literal["fast", "basic", "advanced"] = "advanced"
    max_results: int = Field(default=10, ge=1, le=10)  # 10 are included in the base price
    include_domains: tuple[Domain, ...] = Field(default=(), max_length=20)
    after_date: date | None = None


class ReadPageArgs(_Args):
    """p_search POST /v1/extract for one known page (host must be allow-listed)."""

    url: HttpsUrl
    objective: (
        Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(_clean_text)] | None
    ) = None
    full_content: bool = False


class ScrapeJobPageArgs(_Args):
    """p_scrape POST /v2/scrape. Plain markdown only, and only on job pages."""

    url: HttpsUrl
    page_kind: Literal["job_page"]
    mode: Literal["plain", "json_extraction", "search_with_scrape"] = "plain"
    expensive_mode_reason: Annotated[str, Field(min_length=10, max_length=300)] | None = None

    @model_validator(mode="after")
    def _expensive_needs_reason(self) -> ScrapeJobPageArgs:
        if self.mode != "plain" and self.expensive_mode_reason is None:
            raise ValueError("non-plain scrape modes need expensive_mode_reason (cost reason)")
        return self


class FeedArgs(_Args):
    """p_signals company feed. One logical pull per run; ``since`` makes a delta pull."""

    domain: Domain
    since: date | None = None


class OrgResolveArgs(_Args):
    """Resolve the account's organization (p_org) by its domain."""

    domain: Domain


class OrgTechStackArgs(_Args):
    """The technology stack of one resolved organization (p_org)."""

    organization_id: int = Field(ge=1)


class PeopleByTechnologyArgs(_Args):
    """p_org POST /v9/people, filter mode scoped to one organization and one technology."""

    organization_id: int = Field(ge=1)
    technology_slug: TechSlug


class PeopleByTitleArgs(_Args):
    """People at the account's domain by title (p_people; unsupported until the grid contract is
    supplied)."""

    domain: Domain
    titles: tuple[QueryText, ...] = Field(min_length=1, max_length=20)


class ProfileEnrichmentArgs(_Args):
    """One profile's enrichment by its returned URL (p_people; unsupported until supplied)."""

    profile_url: HttpsUrl


class OrgTeamsArgs(_Args):
    """Teams at the account's domain (p_org)."""

    domain: Domain


OperationArgs = (
    WebSearchArgs
    | ReadPageArgs
    | ScrapeJobPageArgs
    | FeedArgs
    | OrgResolveArgs
    | OrgTechStackArgs
    | PeopleByTechnologyArgs
    | PeopleByTitleArgs
    | ProfileEnrichmentArgs
    | OrgTeamsArgs
)

ARGS_FOR: dict[Operation, type[_Args]] = {
    Operation.WEB_SEARCH: WebSearchArgs,
    Operation.READ_PAGE: ReadPageArgs,
    Operation.SCRAPE_JOB_PAGE: ScrapeJobPageArgs,
    Operation.NEWS_EVENTS: FeedArgs,
    Operation.JOB_OPENINGS: FeedArgs,
    Operation.TECH_DETECTIONS: FeedArgs,
    Operation.ORG_RESOLVE: OrgResolveArgs,
    Operation.ORG_TECH_STACK: OrgTechStackArgs,
    Operation.PEOPLE_BY_TECHNOLOGY: PeopleByTechnologyArgs,
    Operation.PEOPLE_BY_TITLE: PeopleByTitleArgs,
    Operation.PROFILE_ENRICHMENT: ProfileEnrichmentArgs,
    Operation.ORG_TEAMS: OrgTeamsArgs,
}

# Operations whose target URL is a page the provider will read: host must be allow-listed.
PAGE_READING = frozenset({Operation.READ_PAGE, Operation.SCRAPE_JOB_PAGE})


# --------------------------------------------------------------------------- records


class Provenance(BaseModel):
    """Mandatory on every record. ``source_url`` is as returned by the provider (or None)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_code: ProviderCode
    operation: Operation
    source_url: str | None
    retrieved_at: datetime
    raw_ref: str = Field(min_length=1)
    page: int = Field(ge=1)
    index: int = Field(ge=0)

    @field_validator("retrieved_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("retrieved_at must be an aware UTC datetime")
        return value


class Record(BaseModel):
    """Base record. No defaults: every field must be given, None when the provider omits it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    provenance: Provenance
    # Upstream origin the provider itself states (e.g. the article behind a news event);
    # never guessed (open decision D-11).
    lineage_hint: str | None

    def dedupe_key(self) -> str:
        """Stable key for de-duplicating records across pages."""
        raise NotImplementedError


class WebPageRecord(Record):
    """A search hit or page read: URL, title, excerpts and optional full content."""

    kind: Literal["web_page"]
    url: str
    title_text: str | None
    publish_date: str | None
    excerpts_text: tuple[str, ...]
    full_content_text: str | None

    def dedupe_key(self) -> str:
        """The page URL."""
        return self.url


class ScrapedPageRecord(Record):
    """A plain scrape of a job page: requested and final URL, status and markdown text."""

    kind: Literal["scraped_page"]
    requested_url: str | None
    final_url: str | None
    target_status: int | None
    markdown_text: str | None
    title_text: str | None

    def dedupe_key(self) -> str:
        """The final URL, else the requested URL, else the payload reference."""
        return self.final_url or self.requested_url or self.provenance.raw_ref


class NewsEventRecord(Record):
    """A structured news event from the company feed, with its article sentence."""

    kind: Literal["news_event"]
    event_id: str
    category: str
    summary_text: str
    found_at: str
    confidence: float
    article_sentence_text: str | None
    effective_date: str | None
    amount_normalized_usd: int | None
    planning: bool
    article_url: str | None
    article_title_text: str | None
    article_published_at: str | None

    def dedupe_key(self) -> str:
        """The provider's event id."""
        return self.event_id


class JobOpeningRecord(Record):
    """One job opening from the company feed (description completeness is not stated)."""

    kind: Literal["job_opening"]
    job_id: str
    title_text: str
    normalized_title: str | None
    description_text: str | None
    url: str
    first_seen_at: str
    last_seen_at: str
    posted_at: str | None
    status: str | None
    seniority: str | None
    location: str | None
    categories: tuple[str, ...]

    def dedupe_key(self) -> str:
        """The provider's job id."""
        return self.job_id


class TechDetectionRecord(Record):
    """A technology detection with first and last seen dates (a date range alone is a Lead)."""

    kind: Literal["tech_detection"]
    detection_id: str
    technology_id: str | None
    technology_name: str | None  # None when the referenced object is not in `included`
    first_seen_at: str
    last_seen_at: str
    score: float
    source_count: int
    behind_firewall: bool
    evidence_urls: tuple[str, ...]

    def dedupe_key(self) -> str:
        """The provider's detection id."""
        return self.detection_id


class OrganizationRecord(Record):
    """A resolved organization (id, name, industry, headquarters country)."""

    kind: Literal["organization"]
    input_domain: str
    matched: bool
    organization_id: int | None
    slug: str | None
    name: str | None
    url: str | None
    employee_count: int | None
    industry: str | None
    headquarters_country: str | None

    def dedupe_key(self) -> str:
        """The organization id, else the input domain."""
        return str(self.organization_id) if self.organization_id is not None else self.input_domain


class TechStackRecord(Record):
    """One technology in an organization's stack, by business function and category."""

    kind: Literal["tech_stack_item"]
    organization_id: int
    business_function: str
    category_slug: str
    category_name: str
    technology_name: str
    technology_slug: str
    job_post_used_count: int

    def dedupe_key(self) -> str:
        """Business function, category and technology slugs."""
        return f"{self.business_function}/{self.category_slug}/{self.technology_slug}"


class PersonRecord(Record):
    """A person returned by a people search (profile URL first, provider id second)."""

    kind: Literal["person"]
    provider_person_id: str
    profile_url: str | None  # dedupe key for people (PRD); None when the provider has none
    name_text: str | None
    job_title_text: str | None
    job_function: str | None
    job_level: str | None
    location: str | None

    def dedupe_key(self) -> str:
        """The profile URL, else the provider person id."""
        return self.profile_url or f"provider:{self.provider_person_id}"
