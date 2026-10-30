"""``ProviderPort`` on the connector layer (``config/skill/provider_routes.yaml``).

* A need maps to a platform route and a typed operation; arguments are built in code (search
  queries, the account's domain for feeds and people) and validated by the connector's models.
* A page read or scrape first searches the account's own site and reads the first result on the
  account's domain (page reads are allow-listed to the account's hosts by the connector).
* A web search reads the account's own pages it found (scrape) and the first pages it found on
  other sites (page read, that host allowed for that one read; owner decision 2026-10-21).
* An ``own_site`` row (``pull_plan.yaml``) searches its query on the account's own site as well
  and reads what it finds there in full (a live scrape of the newsroom release, the results
  release, the leadership page): the primary-source path that makes a fact Confirmed (G5). A
  feed row with ``own_site`` (news, leadership) pulls the feed once and adds the same own-site
  search for each of its queries (phase1-pull.md step 2: the newsroom list read, releases of the
  last 18 months). Until 2026-10-24 no fact of any run was Confirmed (audit).
* Concurrency (2026-10-30): the independent calls of one query run together (the open search, the
  own-site search and the restricted ``also_on`` search; then every page read they ask for), and
  results are assembled in the order the one-at-a-time code produced them. What reaches a provider
  passes the research's own gate for that provider, sized by the governor's ceiling
  (``crosscut.gates``, ``GATE_OF_ROUTE``): the waiting is here, not in the governor's bounded wait.
* ``follow`` (``pull_plan.yaml``): an own-site row also reads the links its own-site pages name
  (``agents.production.links``: own domains only, the row's words and window, a few per query, one
  level deep). ``also_on``: the query also searched on named hosts (a register, the newswires),
  bounded to a window, the first pages found there read in full.
* Every record becomes one ``SourceItem`` with the record's link and the same text the grading
  engine re-parses (``grading.adapters.record_text``); a technology detection is a date range.
* Errors: a credit-gate refusal (``CapRefused``, ``BudgetExceeded``) is ``CapPause``; a retryable
  provider error is re-raised for the node ``RetryPolicy`` (the single retry owner); any other
  connector error, an unsupported operation or a blocked route is ``ProviderFailed`` with a
  neutral reason code. Profile enrichment needs profile URLs from an earlier pull: Blocked.

GUARANTEE(test_runner_executes_an_abm_run_end_to_end_on_postgres)
GUARANTEE(test_connector_provider_maps_refusals_and_failures)
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.connectors.base import (
    SINGLE_CALL_CEILING_S,
    CallContext,
    ConnectorResult,
    Operation,
)
from account_intelligence.connectors.errors import (
    BudgetExceeded,
    CapRefused,
    ConnectorError,
    RetryableProviderError,
)
from account_intelligence.connectors.models import (
    FeedArgs,
    NewsEventRecord,
    OrganizationRecord,
    OrgResolveArgs,
    OrgTechStackArgs,
    PeopleByTechnologyArgs,
    PeopleByTitleArgs,
    ReadPageArgs,
    Record,
    ScrapedPageRecord,
    ScrapeJobPageArgs,
    TechDetectionRecord,
    TechStackRecord,
    WebPageRecord,
    WebSearchArgs,
)
from account_intelligence.connectors.router import call_need
from account_intelligence.connectors.service import ConnectorRuntime, call_operation
from account_intelligence.grading.adapters import record_text
from account_intelligence.grading.config import load_grading_config
from agents.config import SkillConfig, skill_config
from agents.entity import EntityLock, brand_token, build_lock
from agents.ports import CapPause, ProviderFailed, ProviderResult, RunContext, SourceItem
from agents.production.links import followed_links
from crosscut.fanout import fan_out_all
from crosscut.gates import ProviderGates
from jobs.window import months_before
from people.config import load_people_config
from people.technology import technologies_by_lane
from spend.money import usd_to_micros

_CODE = re.compile(r"[^a-z0-9]+")


def reason_code(error: BaseException) -> str:
    """A neutral snake_case reason from the error's class (never a provider name or message)."""
    name = type(error).__name__
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return _CODE.sub("_", snake).strip("_") or "source_failed"


# full reads of the account's own pages per web search (D-30 allow-list; lifted when the research
# caps are off, dev_limits)
OWN_PAGE_READS_PER_SEARCH = 3
# full reads of other sites' pages per web search: news, filings, transcripts, interviews and
# vendor stories the search found are read, not left as snippets (owner decision 2026-10-21,
# lifting D-30 for pages a search of this run returned; the skill reads them in full). They stay
# below Confirmed unless the grading engine ranks the host a primary source.
OFF_SITE_READS_PER_SEARCH = 2
# hosts a page read cannot open (sign-in walls): searched for snippets only
UNREADABLE_HOSTS = frozenset(
    {"linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com"}
)
# characters kept of one page read (one long page must not fill an extraction call alone); with
# the research caps off the whole page is kept up to the extraction call's size
MAX_READ_CHARS = 24_000
UNCAPPED_READ_CHARS = 120_000
# Page reads of one search that run side by side. What bounds the calls in flight is the
# per-provider gate below (the governor's ceiling), so this only has to be large enough to keep
# the gates busy; a search of ten results used to read its pages one after another, 2 to 17 s
# each (staging 2026-10-27 speed audit), and held its row for minutes.
READS_IN_FLIGHT = 8
# The provider behind each routed need whose calls hold a gate (``providers.yaml routes``). A
# research makes at most the governor's ceiling for that provider (``config/parallel.yaml``
# ``providers.ceilings``) of these calls at once, however many rows, queries and pages wait: the
# rest wait here, without a time limit, instead of in the governor, whose bounded wait ends the
# slice (``SlotUnavailable``) when many callers queue for a few slots.
GATE_OF_ROUTE = {
    "open_web_search": "p_search",
    "read_known_page": "p_search",
    "job_boards_and_pages": "p_scrape",
    "people_by_technology": "p_org",
}


async def together(*calls: Awaitable[Any]) -> list[Any]:
    """Every call finished (none cancelled for a sibling's failure: a cancelled paid call is paid
    again when the node replays), then the first failure, in argument order, raised as itself.
    The results are in argument order."""
    done = await asyncio.gather(*calls, return_exceptions=True)
    for outcome in done:
        if isinstance(outcome, BaseException):
            raise outcome
    return list(done)


@dataclass(frozen=True)
class RoutesConfig:
    """The validated translation table."""

    needs: Mapping[str, Mapping[str, Any]]
    feed_by_row: Mapping[str, str]
    people_titles: tuple[str, ...]
    attribute_prefix: Mapping[str, str]


@lru_cache(maxsize=2)
def load_routes(path: Path | None = None) -> RoutesConfig:
    """Read ``config/skill/provider_routes.yaml``."""
    doc = yaml.safe_load((path or CONFIG_DIR / "skill" / "provider_routes.yaml").read_text())
    return RoutesConfig(
        needs={k: v["value"] for k, v in doc["needs"].items()},
        feed_by_row=dict(doc["feed_operation_by_row"]["value"]),
        people_titles=tuple(doc["people_titles"]["value"]),
        attribute_prefix=dict(doc["attribute_prefix_by_row"]["value"]),
    )


def items_of(records: tuple[Record, ...], max_chars: int | None = None) -> tuple[SourceItem, ...]:
    """One item per record with a link and text (records without a link cannot be cited, G4);
    ``max_chars`` keeps the start of a long page (a quote from it is still in the record the
    grading engine re-reads)."""
    out = []
    for r in records:
        url = r.provenance.source_url
        body = record_text(r)
        if max_chars is not None and body:
            body = body[:max_chars]
        if url and body:
            kind = "date_range" if isinstance(r, TechDetectionRecord) else None
            out.append(SourceItem(url, body, kind, _meta(r)))
    return tuple(out)


def _meta(r: Record) -> tuple[tuple[str, str], ...]:
    """What a record states about itself, for the structured archive copy of its address."""
    pairs: list[tuple[str, str | None]]
    if isinstance(r, NewsEventRecord):
        pairs = [
            ("record", "news"),
            ("title", r.article_title_text),
            ("summary", r.summary_text),
            ("published", r.article_published_at or r.found_at),
            ("category", r.category),
        ]
    elif isinstance(r, WebPageRecord | ScrapedPageRecord):
        pairs = [("record", "page"), ("title", r.title_text), ("published", r.publish_date)]
    else:
        pairs = []
    return tuple((k, v) for k, v in pairs if v)


@dataclass
class ConnectorProvider:
    """See the module docstring. One instance per run."""

    rt: ConnectorRuntime
    account_id: UUID
    lanes: tuple[str, ...] = ()
    extra_allowed_hosts: frozenset[str] = frozenset()
    routes: RoutesConfig = field(default_factory=load_routes)
    # research cache: finished calls of earlier runs of this tenant may be adopted (False for a
    # run started with run_input {"fresh": true})
    reuse_cached: bool = True
    resume_from: UUID | None = None
    # the run's date: the 12-month posting window is anchored to its month (the call key, and so
    # its reuse on resume or a re-run, stays the same all month; the job pipeline anchors the
    # same way, jobs.adapters); None is today
    as_of: date | None = None
    # the skill config (which rows are own-site rows); loaded once when not given
    skill: SkillConfig | None = None
    # an incremental re-research's job feed bound (``jobs.incremental.postings_since`` of the
    # run input); None pulls the skill's 12-month posting window
    postings_since: date | None = None
    # the provider gates (``GATE_OF_ROUTE``): this run's own, never shared with another research
    _gates: ProviderGates = field(default_factory=ProviderGates, init=False, repr=False)

    def _own_site(self, row_key: str) -> bool:
        cfg = self.skill
        if cfg is None:
            from agents.config import skill_config

            cfg = self.skill = skill_config()
        plan = cfg.plan.get(row_key)
        return bool(plan is not None and plan.own_site)

    def _gate(self, route: str) -> asyncio.Semaphore | None:
        """The gate of the provider behind ``route`` (None: not gated)."""
        code = GATE_OF_ROUTE.get(route)
        return None if code is None else self._gates(code)

    async def _call(
        self,
        route: str,
        args: Any,
        cc: CallContext,
        *,
        operation: Operation | None = None,
    ) -> ConnectorResult:
        """``call_need`` through the provider's gate (see ``GATE_OF_ROUTE``)."""
        extra = {} if operation is None else {"operation": operation}
        gate = self._gate(route)
        if gate is None:
            return await call_need(route, args, cc, self.rt, **extra)
        async with gate:
            return await call_need(route, args, cc, self.rt, **extra)

    def _ctx(self, ctx: RunContext) -> CallContext:
        # the entity lock's other domains of the account (the host its site redirects to,
        # subsidiaries) are read and searched like the account domain itself
        extra = frozenset((*self.extra_allowed_hosts, *ctx.allowed_domains))
        return CallContext(
            tenant_id=ctx.tenant_id,
            run_id=ctx.run_id,
            account_id=self.account_id,
            account_domain=ctx.domain,
            extra_allowed_hosts=extra,
            reuse_cached=self.reuse_cached,
            resume_from=self.resume_from,
        )

    @staticmethod
    def _site_domains(ctx: RunContext) -> tuple[str, ...]:
        """The domains an own-site search covers: the account's and the lock's other domains."""
        return tuple(dict.fromkeys((ctx.domain, *ctx.allowed_domains)))

    @staticmethod
    def _own_url(url: str, ctx: RunContext) -> bool:
        return any(_on_domain(url, d) for d in (ctx.domain, *ctx.allowed_domains))

    async def entity_lock(self, ctx: RunContext) -> EntityLock:
        """Master prompt v2 section 3, before any other pull: the account's home page read live
        (does it name the account? where does it redirect?) and one brand search whose hits
        reveal look-alike brands (vizent.co for Vizient). An unreadable home page is recorded as
        a problem, never treated as a mismatch: only a page that was read and does not name the
        account stops the run."""
        scope = (self.skill or skill_config()).scope
        cc = self._ctx(ctx)
        home = f"https://{ctx.domain}/"
        final_url: str | None = None
        title: str | None = None
        body: str | None = None
        read_ok = False
        hits: list[tuple[str, str]] = []
        brand = brand_token(ctx.account_name, scope.name_stopwords)

        async def read_home() -> Any:
            """The account's home page, read live."""
            # each of the two calls is waited for a bounded time: the lock is the first thing a
            # run does, and a home page that never answers is "unreadable", not a hung run (F9)
            async with asyncio.timeout(SINGLE_CALL_CEILING_S):
                return await call_need(
                    "job_boards_and_pages",
                    ScrapeJobPageArgs(url=home, page_kind="account_page"),
                    cc,
                    self.rt,
                )

        async def brand_search() -> Any:
            """One brand search, to reveal look-alike brands."""
            async with asyncio.timeout(SINGLE_CALL_CEILING_S):
                return await call_need(
                    "open_web_search",
                    WebSearchArgs(queries=(f'"{brand}" company', ctx.account_name[:200])),
                    cc,
                    self.rt,
                )

        # the home page and the brand search do not depend on each other: they run together
        outcomes: tuple[Any, Any] = await asyncio.gather(
            read_home(), brand_search(), return_exceptions=True
        )
        for outcome in outcomes:
            if isinstance(outcome, (CapRefused, BudgetExceeded)):
                raise outcome
            if isinstance(outcome, BaseException) and not isinstance(
                outcome, (ConnectorError, TimeoutError)
            ):
                raise outcome
        read, search = outcomes
        if not isinstance(read, BaseException):
            page = next((r for r in read.records if isinstance(r, ScrapedPageRecord)), None)
            if page is not None and (page.markdown_text or page.title_text):
                final_url, title = page.final_url, page.title_text
                body = (page.markdown_text or "")[:50_000]
                read_ok = True
        if not isinstance(search, BaseException):
            hits = [
                (r.url, r.title_text or "") for r in search.records if isinstance(r, WebPageRecord)
            ]
        lock = build_lock(
            legal_name=ctx.account_name,
            primary_domain=ctx.domain,
            stopwords=scope.name_stopwords,
            home_url=home,
            final_url=final_url,
            home_title=title,
            home_text=body,
            search_hits=hits,
            max_distance=scope.lookalike_edit_distance,
        )
        if not read_ok:
            problems = tuple(x for x in lock.problems if "does not name" not in x)
            lock = replace(lock, problems=(*problems, f"home page of {ctx.domain} unreadable"))
        return lock

    async def call(self, ctx: RunContext, need: str, query: str, row_key: str) -> ProviderResult:
        """One routed pull for a coverage row (see the module docstring)."""
        spec = self.routes.needs.get(need)
        if spec is None:
            raise ProviderFailed("need_not_routed")
        cc = self._ctx(ctx)
        try:
            results = await self._run(spec, cc, ctx, query, row_key)
        except CapRefused as refused:
            raise CapPause(refused.cap_reason) from None
        except BudgetExceeded:
            raise CapPause("cap_reached") from None
        except RetryableProviderError:
            raise  # the node RetryPolicy owns retries (W6)
        except ConnectorError as error:
            raise ProviderFailed(reason_code(error)) from None
        blocked = [r for r in results if r.status == "blocked"]
        if blocked and len(blocked) == len(results):
            raise ProviderFailed("route_blocked")  # the route's text names a provider: never used
        records = tuple(rec for r in results for rec in r.records)
        reads = tuple(
            rec for r in results if r.operation is Operation.READ_PAGE for rec in r.records
        )
        others = tuple(
            rec for r in results if r.operation is not Operation.READ_PAGE for rec in r.records
        )
        category = load_grading_config().category_by_operation
        op = next((r.operation for r in results if r.operation), None)
        from account_intelligence.dev_limits import research_uncapped

        # caps off: a long page (a filing, a transcript) is kept whole up to the extraction
        # call's own size; the extractor splits what it reads, nothing is cut away unread
        kept = UNCAPPED_READ_CHARS if research_uncapped() else MAX_READ_CHARS
        return ProviderResult(
            items=items_of(others) + items_of(reads, kept),
            result_count=len(records),
            cost_micros=sum(usd_to_micros(Decimal(str(r.charged_usd))) for r in results),
            tool_category=category.get(op.value if op else "", need),
            call_refs=tuple(ref for r in results for ref in r.call_refs),
        )

    async def _run(
        self, spec: Mapping[str, Any], cc: CallContext, ctx: RunContext, query: str, row: str
    ) -> list[ConnectorResult]:
        route = str(spec["route"])
        operation = Operation(str(spec["operation"]))
        if spec.get("needs") == "profile_urls":
            raise ProviderFailed("needs_profile_urls")
        if spec.get("url_from") == "account_search":
            search = await self._call(
                "open_web_search",
                WebSearchArgs(queries=(query[:200],), include_domains=self._site_domains(ctx)),
                cc,
            )
            urls = [
                r.url
                for r in search.records
                if isinstance(r, WebPageRecord) and self._own_url(r.url, ctx)
            ]
            if not urls:
                return [search]
            args: Any = (
                ScrapeJobPageArgs(url=urls[0], page_kind="job_page")
                if operation is Operation.SCRAPE_JOB_PAGE
                else ReadPageArgs(url=urls[0], full_content=True)
            )
            return [search, await self._call(route, args, cc)]
        if route == "news_tech_job_feeds":
            feed = Operation(self.routes.feed_by_row.get(row, str(spec["operation"])))
            # job postings are a 12-month row: an unbounded feed pages the whole history
            # anchored to the month, not the day: the call key (and so its reuse on resume or
            # a re-run) stays the same all month instead of changing daily (audit 2026-10-15)
            # the feed windows of the skill (scope.yaml: news 18, postings 12, detections 24
            # months), anchored to the run's month so the call key repeats within a month
            since = feed_since(
                feed, self.month_anchor(), (self.skill or skill_config()).scope, self.postings_since
            )
            feed_call = self._call(
                route, FeedArgs(domain=ctx.domain, since=since), cc, operation=feed
            )
            if not self._own_site(row):
                return [await feed_call]
            # the feed (pages paged one after another) is joined by the company's own newsroom
            # or leadership pages, read in full (phase1-pull.md step 2), and by the newswires
            # that carry its releases (``also_on``); the query is the row's own (pull_plan
            # queries). They are independent: the feed pages while the others are searched and
            # read.
            out, site, wires = await together(
                feed_call,
                self._own_site_search(cc, ctx, query, row),
                self._also_on(cc, ctx, row, query),
            )
            return [out, *site, *wires]
        if operation is Operation.PEOPLE_BY_TITLE:
            args = PeopleByTitleArgs(domain=ctx.domain, titles=self.routes.people_titles)
            return [await self._call(route, args, cc)]
        if spec.get("needs") == "organization_id":
            org = await call_operation(
                Operation.ORG_RESOLVE, OrgResolveArgs(domain=ctx.domain), cc, self.rt
            )
            ids = [
                r.organization_id
                for r in org.records
                if isinstance(r, OrganizationRecord) and r.matched and r.organization_id
            ]
            if not ids:
                return [org]
            # the account's own stack, matched to each lane by name (people.technology)
            stack = await call_operation(
                Operation.ORG_TECH_STACK, OrgTechStackArgs(organization_id=ids[0]), cc, self.rt
            )
            items = [r for r in stack.records if isinstance(r, TechStackRecord)]
            per_lane = technologies_by_lane(
                self.lanes, items, load_people_config().technologies_per_lane
            )
            slugs = list(dict.fromkeys(s for slugs in per_lane.values() for s in slugs))
            # one people lookup per technology, side by side (the provider's slots bound them)
            people = await together(
                *(
                    self._call(
                        route,
                        PeopleByTechnologyArgs(organization_id=ids[0], technology_slug=slug),
                        cc,
                    )
                    for slug in slugs
                )
            )
            return [org, stack, *people]
        # the open search and the account's own-site search of the same query are independent: they
        # run together, then every page read of both runs together (the reads were one after
        # another, each 2 to 17 s, and a search of ten results held its row for minutes)
        own_site = self._own_site(row)
        found = await together(
            self._call("open_web_search", WebSearchArgs(queries=(query[:200],)), cc),
            self._also_on(cc, ctx, row, query),
            *([self._site_search(cc, ctx, query)] if own_site else []),
        )
        search, wires = found[0], found[1]
        site_search = found[2] if own_site else None
        own_urls = self._own_page_urls(search, ctx)
        off_urls = self._off_site_urls(search, ctx)
        site_urls = (
            self._own_page_urls(site_search, ctx, skip=set(own_urls[: self._own_limit()]))
            if site_search is not None
            else []
        )
        own_reads, off_reads, site_reads = await together(
            self._read_own_pages(own_urls, cc),
            self._read_off_site_pages(off_urls, cc, query),
            self._read_own_pages(site_urls, cc),
        )
        out = [search, *own_reads, *off_reads]
        if site_search is not None:
            out += [site_search, *site_reads]
        out += wires
        if own_site:
            out += await self._follow_links(cc, ctx, row, [*own_reads, *site_reads], out)
        return out

    def month_anchor(self) -> date:
        """The first day of the run's month (``as_of``, else today)."""
        return (self.as_of or date.today()).replace(day=1)

    async def _site_search(self, cc: CallContext, ctx: RunContext, query: str) -> ConnectorResult:
        """The query searched on the account's own site (and the entity lock's other domains)."""
        return await self._call(
            "open_web_search",
            WebSearchArgs(queries=(query[:200],), include_domains=self._site_domains(ctx)),
            cc,
        )

    async def _own_site_search(
        self, cc: CallContext, ctx: RunContext, query: str, row: str
    ) -> list[ConnectorResult]:
        """The query searched on the account's own site, and the pages found there read in full
        (live scrapes, origin full_page_read: the only way a fact becomes Confirmed), then the
        row's followed links (``_follow_links``)."""
        search = await self._site_search(cc, ctx, query)
        reads = await self._read_own_pages(self._own_page_urls(search, ctx), cc)
        out = [search, *reads]
        return out + await self._follow_links(cc, ctx, row, reads, out)

    async def _also_on(
        self, cc: CallContext, ctx: RunContext, row: str, query: str
    ) -> list[ConnectorResult]:
        """The row's query searched on the hosts its ``also_on`` rule names (a register, the
        newswires), bounded to the rule's window, and the first pages found there read in full.
        A page the search returns from any other host is neither read nor kept as a reason to
        read: the rule's hosts are the whole of it."""
        cfg = self.skill or skill_config()
        spec = cfg.plan.get(row)
        rule = spec.also_on if spec is not None else None
        if rule is None:
            return []
        after = (
            months_before(self.month_anchor(), int(cfg.scope.window_months[rule.window]))
            if rule.window
            else None
        )
        search = await self._call(
            "open_web_search",
            WebSearchArgs(queries=(query[:200],), include_domains=rule.domains, after_date=after),
            cc,
        )
        urls = [
            u
            for u in self._off_site_urls(search, ctx)
            if any(_on_domain(u, domain) for domain in rule.domains)
        ]
        return [search, *await self._read_off_site_pages(urls, cc, query, limit=rule.reads)]

    @staticmethod
    def _own_limit() -> int:
        from account_intelligence.dev_limits import UNCAPPED, research_uncapped

        return UNCAPPED if research_uncapped() else OWN_PAGE_READS_PER_SEARCH

    def _own_page_urls(
        self, search: ConnectorResult, ctx: RunContext, skip: set[str] | None = None
    ) -> list[str]:
        """The account's own pages a search found, in the search's order, once each; ``skip``
        leaves out the pages another search of the same query is already reading."""
        return list(
            dict.fromkeys(
                r.url
                for r in search.records
                if isinstance(r, WebPageRecord)
                and self._own_url(r.url, ctx)
                and r.url not in (skip or set())
            )
        )

    @staticmethod
    def _off_site_urls(search: ConnectorResult, ctx: RunContext) -> list[str]:
        """The pages a search found on other sites that can be opened (not sign-in walls)."""
        return list(
            dict.fromkeys(
                r.url
                for r in search.records
                if isinstance(r, WebPageRecord)
                and not _on_domain(r.url, ctx.domain)
                and not _unreadable(r.url)
            )
        )

    async def _read_own_pages(
        self, urls: list[str], cc: CallContext, limit: int | None = None
    ) -> list[ConnectorResult]:
        """Full reads of the account's own pages a search found (the first ``limit``: three, all
        with the caps off), side by side and returned in the search's order. A snippet is never
        more than a Lead (G5): only a full page read in a primary source (the company's own site
        is T1) can make a fact Confirmed. The read is a live scrape (maxAge 0, origin
        full_page_read): an extract read may be a cached copy and never qualifies. Other hosts stay
        snippets (D-30)."""

        async def read(url: str) -> ConnectorResult | None:
            try:
                return await self._call(
                    "job_boards_and_pages",
                    ScrapeJobPageArgs(url=url, page_kind="account_page"),
                    cc,
                )
            except (CapRefused, BudgetExceeded):
                raise
            except ConnectorError:
                # one unreadable or slow page (a timeout after the connector's own retries)
                # never loses the search it came from, nor fails the slice and replays the pull
                return None

        done = await fan_out_all(
            urls[: self._own_limit() if limit is None else limit], read, limit=READS_IN_FLIGHT
        )
        return [r for r in done if r is not None]

    async def _read_off_site_pages(
        self, urls: list[str], cc: CallContext, query: str, limit: int | None = None
    ) -> list[ConnectorResult]:
        """Full reads of the first pages a search found on other sites (see
        ``OFF_SITE_READS_PER_SEARCH``), side by side and returned in the search's order. Only a
        page this search returned is opened: its host is allowed for that one read, and the
        connector's SSRF checks (public addresses, https, the final URL after redirects) still
        apply. A page that cannot be read keeps its snippet."""
        from account_intelligence.dev_limits import UNCAPPED, research_uncapped

        if limit is None:
            limit = UNCAPPED if research_uncapped() else OFF_SITE_READS_PER_SEARCH

        async def read(url: str) -> ConnectorResult | None:
            host = (urlsplit(url).hostname or "").lower().rstrip(".")
            one = replace(cc, extra_allowed_hosts=cc.extra_allowed_hosts | {host})
            try:
                if _primary_registry_host(host):
                    # a filing or release on a first-tier register (sec.gov) is scraped live,
                    # the one read the engine counts as a primary read this run, so a figure
                    # read there can be Confirmed (2026-10-24); an extract read is a cached copy
                    return await self._call(
                        "job_boards_and_pages",
                        ScrapeJobPageArgs(url=url, page_kind="account_page"),
                        one,
                    )
                return await self._call(
                    "read_known_page",
                    ReadPageArgs(url=url, objective=query[:2000], full_content=True),
                    one,
                )
            except (CapRefused, BudgetExceeded):
                raise
            except ConnectorError:
                return None  # paywalled, refused or slow: the search snippet stays

        done = await fan_out_all(urls[:limit], read, limit=READS_IN_FLIGHT)
        return [r for r in done if r is not None]

    async def _follow_links(
        self,
        cc: CallContext,
        ctx: RunContext,
        row: str,
        pages: list[ConnectorResult],
        already: list[ConnectorResult],
    ) -> list[ConnectorResult]:
        """The links of the account's own pages that the row follows (``pull_plan.yaml``
        ``follow``): a newsroom list or a release names the releases around it, a leadership or
        investor page the pages beside it. Only links on the account's own domains (the entity
        lock's) whose address carries one of the row's words and whose dated address is inside
        the row's window, never a page already read, at most ``per_query`` of them, read in full
        like any own page (one level deep: a followed page is not followed again)."""
        cfg = self.skill or skill_config()
        spec = cfg.plan.get(row)
        rule = spec.follow if spec is not None else None
        if rule is None or not pages:
            return []
        floor = (
            months_before(self.month_anchor(), int(cfg.scope.window_months[rule.window]))
            if rule.window
            else None
        )
        texts = [
            (base, getattr(rec, "markdown_text", None) or "")
            for page in pages
            for rec in page.records
            if isinstance(rec, ScrapedPageRecord)
            for base in [rec.final_url or rec.requested_url]
            if base
        ]
        chosen = followed_links(
            texts,
            lambda url: self._own_url(url, ctx),
            rule,
            {u for result in already for u in _page_urls(result)},
            floor,
        )
        return await self._read_own_pages(chosen, cc, limit=rule.per_query)


def feed_since(
    feed: Operation, anchor: date, scope: Any, postings_since: date | None = None
) -> date | None:
    """The first-seen bound of a p_signals feed pull: the skill's window for that feed, counted
    back from the run's month anchor; None for a feed without a window. An incremental
    re-research's delta bound (``postings_since``, decided once at run creation and read by
    ``jobs.incremental.postings_since``) replaces the window for the job openings feed only, so
    Phase 1 and the job pipeline share one ledger key for the pull."""
    if feed is Operation.JOB_OPENINGS and postings_since is not None:
        return postings_since
    months = scope.window_months
    key = {
        Operation.JOB_OPENINGS: "postings",
        Operation.NEWS_EVENTS: "news",
        Operation.TECH_DETECTIONS: "detections",
    }.get(feed)
    return months_before(anchor, int(months[key])) if key else None


def _page_urls(result: ConnectorResult) -> set[str]:
    """The page URLs a read returned (a scraped page carries requested and final URLs)."""
    out: set[str] = set()
    for record in getattr(result, "records", ()):
        for name in ("url", "final_url", "requested_url"):
            value = getattr(record, name, None)
            if value:
                out.add(str(value))
    return out


@lru_cache(maxsize=1)
def _primary_hosts() -> tuple[str, ...]:
    """The first-tier source domains of the registry (``config/source_registry.yaml``)."""
    return tuple(d.domain.lower() for d in load_grading_config().registry.domains if d.tier == "T1")


def _primary_registry_host(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in _primary_hosts())


def _unreadable(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".").removeprefix("www.")
    return any(host == h or host.endswith("." + h) for h in UNREADABLE_HOSTS)


def _on_domain(url: str, domain: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host == domain or host.endswith("." + domain)
