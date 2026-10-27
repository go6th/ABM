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

import re
from collections.abc import Mapping
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
from account_intelligence.connectors.base import CallContext, ConnectorResult, Operation
from account_intelligence.connectors.errors import (
    BudgetExceeded,
    CapRefused,
    ConnectorError,
    RetryableProviderError,
)
from account_intelligence.connectors.models import (
    FeedArgs,
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
from crosscut.fanout import fan_out_all
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
# page reads of one search in flight at once (a read takes two to seventeen seconds; with the
# caps off every page a search returned is read, one after another until 2026-10-27)
READ_CONCURRENCY = 4
# characters kept of one page read (one long page must not fill an extraction call alone); with
# the research caps off the whole page is kept up to the extraction call's size
MAX_READ_CHARS = 24_000
UNCAPPED_READ_CHARS = 120_000


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
            out.append(SourceItem(url, body, kind))
    return tuple(out)


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

    def _own_site(self, row_key: str) -> bool:
        cfg = self.skill
        if cfg is None:
            from agents.config import skill_config

            cfg = self.skill = skill_config()
        plan = cfg.plan.get(row_key)
        return bool(plan is not None and plan.own_site)

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
        try:
            read = await call_need(
                "job_boards_and_pages",
                ScrapeJobPageArgs(url=home, page_kind="account_page"),
                cc,
                self.rt,
            )
            page = next((r for r in read.records if isinstance(r, ScrapedPageRecord)), None)
            if page is not None and (page.markdown_text or page.title_text):
                final_url, title = page.final_url, page.title_text
                body = (page.markdown_text or "")[:50_000]
                read_ok = True
        except (CapRefused, BudgetExceeded):
            raise
        except ConnectorError:
            read_ok = False
        hits: list[tuple[str, str]] = []
        brand = brand_token(ctx.account_name, scope.name_stopwords)
        try:
            search = await call_need(
                "open_web_search",
                WebSearchArgs(queries=(f'"{brand}" company', ctx.account_name[:200])),
                cc,
                self.rt,
            )
            hits = [
                (r.url, r.title_text or "") for r in search.records if isinstance(r, WebPageRecord)
            ]
        except (CapRefused, BudgetExceeded):
            raise
        except ConnectorError:
            hits = []
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
            search = await call_need(
                "open_web_search",
                WebSearchArgs(queries=(query[:200],), include_domains=self._site_domains(ctx)),
                cc,
                self.rt,
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
            return [search, await call_need(route, args, cc, self.rt)]
        if route == "news_tech_job_feeds":
            feed = Operation(self.routes.feed_by_row.get(row, str(spec["operation"])))
            # job postings are a 12-month row: an unbounded feed pages the whole history
            # anchored to the month, not the day: the call key (and so its reuse on resume or
            # a re-run) stays the same all month instead of changing daily (audit 2026-10-15)
            # the feed windows of the skill (scope.yaml: news 18, postings 12, detections 24
            # months), anchored to the run's month so the call key repeats within a month
            since = feed_since(feed, self.month_anchor(), (self.skill or skill_config()).scope)
            out = [
                await call_need(
                    route, FeedArgs(domain=ctx.domain, since=since), cc, self.rt, operation=feed
                )
            ]
            if self._own_site(row):
                # the feed is joined by the company's own newsroom or leadership pages, read in
                # full (phase1-pull.md step 2); the query is the row's own (pull_plan queries)
                out += await self._own_site_search(cc, ctx, query)
            return out
        if operation is Operation.PEOPLE_BY_TITLE:
            args = PeopleByTitleArgs(domain=ctx.domain, titles=self.routes.people_titles)
            return [await call_need(route, args, cc, self.rt)]
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
            out = [org, stack]
            for slug in dict.fromkeys(s for slugs in per_lane.values() for s in slugs):
                out.append(
                    await call_need(
                        route,
                        PeopleByTechnologyArgs(organization_id=ids[0], technology_slug=slug),
                        cc,
                        self.rt,
                    )
                )
            return out
        search = await call_need(route, WebSearchArgs(queries=(query[:200],)), cc, self.rt)
        own = await self._read_own_pages(search, cc, ctx)
        out = [search, *own, *await self._read_off_site_pages(search, cc, ctx, query)]
        if self._own_site(row):
            read = {u for result in own for u in _page_urls(result)}
            out += await self._own_site_search(cc, ctx, query, already=read)
        return out

    def month_anchor(self) -> date:
        """The first day of the run's month (``as_of``, else today)."""
        return (self.as_of or date.today()).replace(day=1)

    async def _own_site_search(
        self, cc: CallContext, ctx: RunContext, query: str, already: set[str] | None = None
    ) -> list[ConnectorResult]:
        """The query searched on the account's own site, and the pages found there read in full
        (live scrapes, origin full_page_read: the only way a fact becomes Confirmed). Pages
        ``already`` read by the open search are not read again."""
        search = await call_need(
            "open_web_search",
            WebSearchArgs(queries=(query[:200],), include_domains=self._site_domains(ctx)),
            cc,
            self.rt,
        )
        reads = await self._read_own_pages(search, cc, ctx, skip=already or set())
        return [search, *reads]

    async def _read_own_pages(
        self,
        search: ConnectorResult,
        cc: CallContext,
        ctx: RunContext,
        skip: set[str] | None = None,
    ) -> list[ConnectorResult]:
        """Full reads of the account's own pages a search found. A snippet is never more than a
        Lead (G5): only a full page read in a primary source (the company's own site is T1) can
        make a fact Confirmed. The read is a live scrape (maxAge 0, origin full_page_read): an
        extract read may be a cached copy and never qualifies. Other hosts stay snippets (D-30)."""
        from account_intelligence.dev_limits import UNCAPPED, research_uncapped

        own = list(
            dict.fromkeys(
                r.url
                for r in search.records
                if isinstance(r, WebPageRecord)
                and self._own_url(r.url, ctx)
                and r.url not in (skip or set())
            )
        )
        limit = UNCAPPED if research_uncapped() else OWN_PAGE_READS_PER_SEARCH

        async def read(url: str) -> ConnectorResult | None:
            try:
                return await call_need(
                    "job_boards_and_pages",
                    ScrapeJobPageArgs(url=url, page_kind="account_page"),
                    cc,
                    self.rt,
                )
            except (CapRefused, BudgetExceeded):
                raise
            except ConnectorError:
                # one unreadable or slow page (a timeout after the connector's own retries)
                # never loses the search it came from, nor fails the slice and replays the pull
                return None

        # side by side, in the search's order (a cap refusal is raised once every read is done)
        reads = await fan_out_all(own[:limit], read, limit=READ_CONCURRENCY)
        return [r for r in reads if r is not None]

    async def _read_off_site_pages(
        self, search: ConnectorResult, cc: CallContext, ctx: RunContext, query: str
    ) -> list[ConnectorResult]:
        """Full reads of the first pages a search found on other sites (see
        ``OFF_SITE_READS_PER_SEARCH``). Only a page this search returned is opened: its host is
        allowed for that one read, and the connector's SSRF checks (public addresses, https, the
        final URL after redirects) still apply. A page that cannot be read keeps its snippet."""
        from dataclasses import replace

        urls = list(
            dict.fromkeys(
                r.url
                for r in search.records
                if isinstance(r, WebPageRecord)
                and not _on_domain(r.url, ctx.domain)
                and not _unreadable(r.url)
            )
        )
        from account_intelligence.dev_limits import UNCAPPED, research_uncapped

        limit = UNCAPPED if research_uncapped() else OFF_SITE_READS_PER_SEARCH

        async def read(url: str) -> ConnectorResult | None:
            host = (urlsplit(url).hostname or "").lower().rstrip(".")
            one = replace(cc, extra_allowed_hosts=cc.extra_allowed_hosts | {host})
            try:
                if _primary_registry_host(host):
                    # a filing or release on a first-tier register (sec.gov) is scraped live,
                    # the one read the engine counts as a primary read this run, so a figure
                    # read there can be Confirmed (2026-10-24); an extract read is a cached copy
                    return await call_need(
                        "job_boards_and_pages",
                        ScrapeJobPageArgs(url=url, page_kind="account_page"),
                        one,
                        self.rt,
                    )
                return await call_need(
                    "read_known_page",
                    ReadPageArgs(url=url, objective=query[:2000], full_content=True),
                    one,
                    self.rt,
                )
            except (CapRefused, BudgetExceeded):
                raise
            except ConnectorError:
                return None  # paywalled, refused or slow: the search snippet stays

        # side by side, in the search's order (a cap refusal is raised once every read is done)
        reads = await fan_out_all(urls[:limit], read, limit=READ_CONCURRENCY)
        return [r for r in reads if r is not None]


def feed_since(feed: Operation, anchor: date, scope: Any) -> date | None:
    """The first-seen bound of a p_signals feed pull: the skill's window for that feed, counted
    back from the run's month anchor; None for a feed without a window."""
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
