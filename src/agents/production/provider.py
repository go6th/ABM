"""``ProviderPort`` on the connector layer (``config/skill/provider_routes.yaml``).

* A need maps to a platform route and a typed operation; arguments are built in code (search
  queries, the account's domain for feeds and people) and validated by the connector's models.
* A page read or scrape first searches the account's own site and reads the first result on the
  account's domain (page reads are allow-listed to the account's hosts by the connector).
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
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
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
from agents.ports import CapPause, ProviderFailed, ProviderResult, RunContext, SourceItem
from jobs.window import window_start
from people.config import load_people_config
from people.technology import technologies_by_lane
from spend.money import usd_to_micros

_CODE = re.compile(r"[^a-z0-9]+")


def reason_code(error: BaseException) -> str:
    """A neutral snake_case reason from the error's class (never a provider name or message)."""
    name = type(error).__name__
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return _CODE.sub("_", snake).strip("_") or "source_failed"


# full reads of the account's own pages per web search (D-30 allow-list; lifted on the uncapped
# local stack)
OWN_PAGE_READS_PER_SEARCH = 3


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


def items_of(records: tuple[Record, ...]) -> tuple[SourceItem, ...]:
    """One item per record with a link and text (records without a link cannot be cited, G4)."""
    out = []
    for r in records:
        url = r.provenance.source_url
        body = record_text(r)
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

    def _ctx(self, ctx: RunContext) -> CallContext:
        return CallContext(
            tenant_id=ctx.tenant_id,
            run_id=ctx.run_id,
            account_id=self.account_id,
            account_domain=ctx.domain,
            extra_allowed_hosts=self.extra_allowed_hosts,
        )

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
        category = load_grading_config().category_by_operation
        op = next((r.operation for r in results if r.operation), None)
        return ProviderResult(
            items=items_of(records),
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
                WebSearchArgs(queries=(query[:200],), include_domains=(ctx.domain,)),
                cc,
                self.rt,
            )
            urls = [
                r.url
                for r in search.records
                if isinstance(r, WebPageRecord) and _on_domain(r.url, ctx.domain)
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
            since = window_start(date.today()) if feed is Operation.JOB_OPENINGS else None
            return [
                await call_need(
                    route, FeedArgs(domain=ctx.domain, since=since), cc, self.rt, operation=feed
                )
            ]
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
        return [search, *await self._read_own_pages(search, cc, ctx)]

    async def _read_own_pages(
        self, search: ConnectorResult, cc: CallContext, ctx: RunContext
    ) -> list[ConnectorResult]:
        """Full reads of the account's own pages a search found. A snippet is never more than a
        Lead (G5): only a full page read in a primary source (the company's own site is T1) can
        make a fact Confirmed. The read is a live scrape (maxAge 0, origin full_page_read): an
        extract read may be a cached copy and never qualifies. Other hosts stay snippets (D-30)."""
        from account_intelligence.dev_limits import UNCAPPED, dev_uncapped

        own = list(
            dict.fromkeys(
                r.url
                for r in search.records
                if isinstance(r, WebPageRecord) and _on_domain(r.url, ctx.domain)
            )
        )
        limit = UNCAPPED if dev_uncapped() else OWN_PAGE_READS_PER_SEARCH
        reads = []
        for url in own[:limit]:
            try:
                reads.append(
                    await call_need(
                        "job_boards_and_pages",
                        ScrapeJobPageArgs(url=url, page_kind="account_page"),
                        cc,
                        self.rt,
                    )
                )
            except (CapRefused, BudgetExceeded):
                raise
            except ConnectorError:
                # one unreadable or slow page (a timeout after the connector's own retries)
                # never loses the search it came from, nor fails the slice and replays the pull
                continue
        return reads


def _on_domain(url: str, domain: str) -> bool:
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host == domain or host.endswith("." + domain)
