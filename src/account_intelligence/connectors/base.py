"""Core contracts of the connector layer: codes, operations, context, results.

Provider identity is a code (``p_search`` ...) everywhere in code, keys, logs and
metrics. Vendor names live only in ``registry_internal``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from account_intelligence.connectors.models import OperationArgs, Record


class ProviderCode(StrEnum):
    """Neutral provider codes; vendor names live only in registry_internal."""

    P_SEARCH = "p_search"
    P_SCRAPE = "p_scrape"
    P_SIGNALS = "p_signals"
    P_PEOPLE = "p_people"
    P_ORG = "p_org"


class Operation(StrEnum):
    """Every connector operation; each maps to one provider and one typed argument model."""

    # p_search (sourced: docs/providers/p_search.md)
    WEB_SEARCH = "web_search"
    READ_PAGE = "read_page"
    # p_scrape
    SCRAPE_JOB_PAGE = "scrape_job_page"
    # p_signals: company feeds, pulled once per run
    NEWS_EVENTS = "news_events"
    JOB_OPENINGS = "job_openings"
    TECH_DETECTIONS = "tech_detections"
    # p_people: transport sourced, people/profile contract depends on a workspace grid
    PEOPLE_BY_TITLE = "people_by_title"
    PROFILE_ENRICHMENT = "profile_enrichment"
    # p_org
    ORG_RESOLVE = "org_resolve"
    ORG_TECH_STACK = "org_tech_stack"
    PEOPLE_BY_TECHNOLOGY = "people_by_technology"
    ORG_TEAMS = "org_teams"


OPERATION_PROVIDER: Mapping[Operation, ProviderCode] = {
    Operation.WEB_SEARCH: ProviderCode.P_SEARCH,
    Operation.READ_PAGE: ProviderCode.P_SEARCH,
    Operation.SCRAPE_JOB_PAGE: ProviderCode.P_SCRAPE,
    Operation.NEWS_EVENTS: ProviderCode.P_SIGNALS,
    Operation.JOB_OPENINGS: ProviderCode.P_SIGNALS,
    Operation.TECH_DETECTIONS: ProviderCode.P_SIGNALS,
    Operation.PEOPLE_BY_TITLE: ProviderCode.P_PEOPLE,
    Operation.PROFILE_ENRICHMENT: ProviderCode.P_PEOPLE,
    Operation.ORG_RESOLVE: ProviderCode.P_ORG,
    Operation.ORG_TECH_STACK: ProviderCode.P_ORG,
    Operation.PEOPLE_BY_TECHNOLOGY: ProviderCode.P_ORG,
    Operation.ORG_TEAMS: ProviderCode.P_ORG,
}


class Clock(Protocol):
    """Injected time source. ``now`` is aware UTC; ``monotonic`` is for deadlines."""

    def now(self) -> datetime:
        """Current aware UTC time."""
        ...

    def monotonic(self) -> float:
        """Monotonic seconds for deadlines and pacing."""
        ...


class SystemClock:
    """The real clock (the only place connectors read time)."""

    def now(self) -> datetime:
        """Current aware UTC time."""
        return datetime.now(UTC)

    def monotonic(self) -> float:
        """Monotonic seconds from the operating system."""
        return time.monotonic()


Sleep = Callable[[float], Awaitable[None]]


async def real_sleep(seconds: float) -> None:
    """Sleep for real (production pacing; tests inject a fake)."""
    await asyncio.sleep(seconds)


@dataclass(frozen=True)
class CallContext:
    """Identity of the call, taken from graph state; never from tool arguments."""

    tenant_id: UUID
    run_id: UUID
    account_id: UUID
    account_domain: str
    # Hosts a page-reading operation may target: account domain and its subdomains are
    # implied; add the verified careers host and hosts of earlier validated sources.
    extra_allowed_hosts: frozenset[str] = frozenset()
    # Monotonic deadline of the current slice; loops stop before it.
    deadline: float | None = None
    # Whether finished calls of earlier runs of this tenant may be adopted (research cache);
    # a run started with run_input {"fresh": true} sets this False and pays for every call.
    reuse_cached: bool = True
    # The run this one continues (run_input {"resume_from": "<run id>"}): its finished calls are
    # reused at cost 0 whatever their age, even when the cache is otherwise off.
    resume_from: UUID | None = None


@dataclass(frozen=True)
class RawResponse:
    """One provider HTTP exchange as received. ``body`` is the decoded payload."""

    status_code: int
    content_type: str
    body: bytes
    headers: Mapping[str, str]
    latency_ms: int
    request_path: str  # path only; never the query string (it may carry a key)


@dataclass(frozen=True)
class Cost:
    """Cost of one call: credits and dollars kept separate (known defect 4), with their source."""

    credits: float | None
    usd: float | None
    cost_source: str  # 'documented', 'provider_reported', 'unknown'
    charged_usd: float  # what the run budget was charged (conservative when usd unknown)


StopReason = Literal[
    "exhausted", "max_pages", "max_items", "max_bytes", "deadline", "loop_detected"
]
ResultStatus = Literal["done", "partial", "blocked"]


@dataclass(frozen=True)
class Counts:
    """Every count names its base."""

    read: int  # items received from the provider, across pages, before dedupe
    matched: int  # items kept after dedupe and validation
    pages: int


@dataclass(frozen=True)
class ConnectorResult:
    """What call_operation returns: normalised records, ledger keys, counts with their base and
    cost."""

    status: ResultStatus
    records: tuple[Record, ...] = ()
    raw_refs: tuple[str, ...] = ()
    counts: Counts = Counts(read=0, matched=0, pages=0)
    stop_reason: StopReason | None = None
    cost_usd: float | None = None
    charged_usd: float = 0.0
    credits: float | None = None
    cost_source: str | None = None
    provider_code: ProviderCode | None = None
    operation: Operation | None = None
    route: str | None = None
    fell_back_from: ProviderCode | None = None
    replayed: bool = False
    possible_duplicate: bool = False
    blocked_reason: str | None = None
    # Ledger keys (args_hash, one per page) of the calls behind this result. Stages pass them to
    # the tracker, which reads the proof back from the ledger (T2); never built by hand.
    call_refs: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PageRequest:
    """A single provider request the connector wants executed."""

    method: Literal["GET", "POST"]
    path: str
    params: Mapping[str, str | int | bool] | None = None
    json_body: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ParsedPage:
    """Validated page: normalised records plus what the pagination engine needs."""

    records: tuple[Record, ...]
    items_read: int
    next_cursor: str | None  # None means the provider says there is nothing more
    # Provider-reported credits for this page, when the response carries them.
    provider_credits: float | None = None
    # Async provider job still running: id to poll, records empty.
    pending_job_id: str | None = None
    # Neutral notes the provider returned alongside the data (e.g. a page it could not read).
    warnings: tuple[str, ...] = ()
    # True when the provider returned a document it bills for (credits_per_document).
    document_returned: bool = True


class Connector(Protocol):
    """One provider adapter: builds requests, parses responses, classifies errors (no retries)."""

    code: ProviderCode

    def supports(self, op: Operation) -> bool:
        """Whether this provider implements the operation with a sourced contract."""
        ...

    def build_request(self, op: Operation, args: OperationArgs, cursor: str | None) -> PageRequest:
        """The HTTP request for one page of an operation (cursor None for the first page)."""
        ...

    def poll_request(self, op: Operation, job_id: str) -> PageRequest:
        """The request that polls an asynchronous provider job."""
        ...

    def parse(
        self,
        op: Operation,
        args: OperationArgs,
        raw: RawResponse,
        provenance_base: Mapping[str, Any],
        cursor: str | None,
    ) -> ParsedPage:
        """Pure and deterministic: same raw response, same records."""
        ...

    def classify_error(self, op: Operation, raw: RawResponse) -> Exception:
        """Map a non-success response to the typed error hierarchy (the node decides retries)."""
        ...
