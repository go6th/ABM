"""The raw records the repair builders read. ``readers.py`` fills them from the database; tests
and the offline repair of an existing export fill them directly. Plain frozen dataclasses: no
behaviour, no account names."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class JobRecord:
    """One job posting as the job pipeline (or a research fact) stored it."""

    key: str  # the pipeline's posting key (unique per stored row, not per posting)
    url: str
    title: str = ""
    other_urls: tuple[str, ...] = ()
    location: str = ""
    posted_at: date | None = None
    first_seen: date | None = None
    last_seen: date | None = None
    status: str = ""  # stored word: open / closed / unknown / found / anything
    status_checked: date | None = None  # when a live check of the site last decided the status
    description: str = ""
    requisition_id: str = ""
    seniority: str = ""
    team: str = ""
    function: str = ""
    stored_platforms: tuple[str, ...] = ()  # the pipeline's own platform names
    stored_class: str = ""  # build / admin_owner / working_tool / user / "" (a stored class)
    source_kind: str = "feed"  # feed | live_board | fact
    collected: date | None = None
    from_fact: bool = False  # a research fact, not a pipeline posting row


@dataclass(frozen=True)
class PersonRecord:
    """One person row. ``origin`` says where it came from: ``profile`` and ``people_search`` are
    allowed; ``news_fact`` (extracted from article text) is never a People row."""

    key: str
    name: str
    title: str = ""
    headline: str = ""
    level: str = ""
    lanes: tuple[str, ...] = ()
    priority: str = ""
    profile_url: str = ""
    location: str = ""
    employer: str = ""
    employer_domain: str = ""
    origin: str = "profile"  # profile | people_search | import | news_fact
    found_by: str = ""
    former_si: str = ""
    last_verified: date | None = None
    note: str = ""
    excluded: bool = False
    internal_only: bool = False


@dataclass(frozen=True)
class LeadershipRecord:
    """One executive from press releases and filings."""

    name: str
    role: str
    start: date | None = None
    end: date | None = None
    source_url: str = ""
    source_type: str = ""
    status: str = ""  # Current | Departed | Moved to divested business | Unverified | ""
    note: str = ""


@dataclass(frozen=True)
class TechRecord:
    """One technology detection or posting-derived technology."""

    technology: str
    category: str = ""
    business_function: str = ""
    detections: int = 0  # website / feed detections
    first_seen: str = ""  # as stored (date or timestamp text)
    last_seen: str = ""
    source_url: str = ""
    origin: str = "detection"  # detection | fact
    detected_domain: str = ""
    cross_check: str = ""


@dataclass(frozen=True)
class FactLine:
    """One extracted fact of an article."""

    fact_id: str
    text: str
    grade: str = ""  # confirmed / probable / hypothesis / lead (any case)
    quote: str = ""
    attribute_key: str = ""
    lane: str = ""
    superseded_by_fact: str = ""  # fact id of the newer fact that replaced this one


@dataclass(frozen=True)
class ArticleRecord:
    """One article or filing (key = canonical URL) with its extracted facts."""

    url: str
    title: str = ""  # the page title; "" when never read
    published: date | None = None  # from the page / URL / filing metadata only
    published_kind: str = ""  # page | url | filing | collected | "" (collected never counts)
    collected: datetime | None = None
    publisher: str = ""
    source_type: str = ""
    tier: str = ""  # primary | trade | aggregator | other | ""
    source_code: str = ""
    facts: tuple[FactLine, ...] = ()


@dataclass(frozen=True)
class SourceRecord:
    """One row of the sources register."""

    code: str
    url: str
    title: str = ""
    publisher: str = ""
    published: date | None = None
    kind: str = ""
    collected: datetime | None = None
    used_in: tuple[str, ...] = ()  # "<deliverable> > <section>"
    read_only: bool = False  # a page the run read that was never graded (code "Read")


@dataclass(frozen=True)
class SearchCall:
    """One web search call (one provider, one run)."""

    query: str
    provider: str = ""
    run_at: datetime | None = None
    pages: int = 0
    sites: tuple[str, ...] = ()
    purpose: str = ""  # a coverage item number or label, "" when unknown
    used: bool | None = None  # None: unknown


@dataclass(frozen=True)
class SignalSeed:
    """A signal already stored by the pipeline (signal_events)."""

    title: str
    signal_type: str = ""
    lane: str = ""
    grade: str = ""
    detected: date | None = None
    event_date: date | None = None
    source_codes: tuple[str, ...] = ()
    source_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoverageRecord:
    """One tracker row of the latest run."""

    row_key: str
    item: str
    number: int = 0
    priority: str = ""
    status: str = ""  # Done | Partial | Not done | Blocked | None found | Not checked
    reason: str = ""
    last_run: date | None = None
    evidence_rows: int = 0
    source_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MailItem:
    """One matched mail thread or drive file (the user's own)."""

    title: str
    kind: str = ""
    sender: str = ""
    date: date | None = None
    summary: str = ""
    relevance: str = ""
    link: str = ""


@dataclass(frozen=True)
class DeliverableClaim:
    """What a deliverable section says about a coverage item (for the consistency check)."""

    file: str
    section: str
    row_key: str  # the coverage item this section depends on
    says_done: bool  # the section presents the area as complete
    carries_partial_note: bool = False  # a visible "Partial: <reason>" note is present


@dataclass(frozen=True)
class CitedFact:
    """A Confirmed fact and the source codes it cites (for the citation check)."""

    fact_id: str
    grade: str
    codes: tuple[str, ...] = ()


@dataclass
class RepairInput:
    """Everything the repair stage reads for one workspace."""

    jobs: list[JobRecord] = field(default_factory=list)
    people: list[PersonRecord] = field(default_factory=list)
    leadership: list[LeadershipRecord] = field(default_factory=list)
    tech: list[TechRecord] = field(default_factory=list)
    articles: list[ArticleRecord] = field(default_factory=list)
    sources: list[SourceRecord] = field(default_factory=list)
    searches: list[SearchCall] = field(default_factory=list)
    signal_seeds: list[SignalSeed] = field(default_factory=list)
    coverage: list[CoverageRecord] = field(default_factory=list)
    mail: list[MailItem] = field(default_factory=list)
    claims: list[DeliverableClaim] = field(default_factory=list)
    cited_facts: list[CitedFact] = field(default_factory=list)
    # deliverable file name -> bytes (the current deliverables), internal file name -> bytes
    deliverables: dict[str, bytes] = field(default_factory=dict)
    internal_files: dict[str, bytes] = field(default_factory=dict)
    # counts the deliverables quote, by key ("jobs.account_postings", ...), for reconciliation
    quoted_counts: dict[str, int] = field(default_factory=dict)
