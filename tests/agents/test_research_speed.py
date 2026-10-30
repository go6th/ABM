"""A research reads and pulls its independent sources side by side (staging 2026-10-27).

The pull phase made its provider calls one at a time: 33 planned rows and three or more searches
each, at about three seconds a search. Each call is slowed here, and the whole run must take well
under what one-at-a-time calls would, with the same outcome."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from tests.agents.conftest import Harness

DELAY = 0.03


def slow(target: Any, name: str, delay: float) -> list[int]:
    """Wrap ``target.name`` so each call takes ``delay`` seconds; the list counts the calls."""
    inner = getattr(target, name)
    calls = [0]

    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        await asyncio.sleep(delay)
        return await inner(*args, **kwargs)

    setattr(target, name, wrapped)
    return calls


@pytest.mark.asyncio
async def test_the_pull_phase_overlaps_its_provider_calls(h: Harness) -> None:
    h.grant_consent()
    calls = slow(h.provider, "call", DELAY)
    started = time.monotonic()
    _, state = await h.run("speed-1")
    elapsed = time.monotonic() - started
    assert calls[0] >= 30, calls[0]
    # one at a time the provider calls alone take calls x DELAY
    assert elapsed < calls[0] * DELAY * 0.5, (elapsed, calls[0] * DELAY)
    assert state["outcome"] in ("completed", "partial")


# the pull path: queries, page reads and stored items overlap (speed audit 2026-10-30) ----------

from datetime import UTC, datetime  # noqa: E402
from uuid import uuid4  # noqa: E402

from account_intelligence.connectors.base import ConnectorResult  # noqa: E402
from agents.config import load_skill_config  # noqa: E402
from agents.contracts import BudgetMeter, load_registry  # noqa: E402
from agents.ports import CapPause, ProviderResult, SourceItem  # noqa: E402
from agents.production import provider as provider_module  # noqa: E402
from agents.production.provider import ConnectorProvider  # noqa: E402
from agents.pull.agent import PullAgent, SpendPause  # noqa: E402
from tests.agents.fakes import FakeClock, FakePayloads  # noqa: E402
from tests.agents.test_production_adapters import ctx as run_ctx  # noqa: E402
from tests.agents.test_production_adapters import page, prov  # noqa: E402

CFG = load_skill_config()


@pytest.fixture(autouse=True)
def warm_config_caches() -> None:
    """The first call of a process parses its configuration (tens of milliseconds); the timing
    tests below compare overlapped against one-at-a-time waits of 30 ms and must not pay it."""
    from parallel.config import load_parallel_config

    provider_module._primary_hosts()
    load_parallel_config()


class QueryProvider:
    """A provider that answers each query after its own delay; refusals by query text."""

    def __init__(
        self, delays: dict[str, float] | None = None, refuse: dict[str, str] | None = None
    ):
        self.delays = delays or {}
        self.refuse = refuse or {}
        self.started: list[str] = []

    async def call(self, ctx: Any, need: str, query: str, row_key: str) -> ProviderResult:
        self.started.append(query)
        await asyncio.sleep(self.delays.get(query, DELAY))
        if query in self.refuse:
            raise CapPause(self.refuse[query])
        return ProviderResult(
            items=(SourceItem(f"https://news.example/{abs(hash(query)) % 10**6}", query),),
            result_count=1,
            cost_micros=0,
            tool_category="web_search",
            call_refs=(f"ref-{query}",),
        )


def pull_agent(provider: Any) -> tuple[PullAgent, BudgetMeter, FakePayloads]:
    payloads = FakePayloads()
    clock = FakeClock()
    contract = load_registry()["pull"]
    agent = PullAgent(contract, CFG, provider, payloads, clock, None)
    return agent, BudgetMeter(contract, started_at=clock.now()), payloads


@pytest.mark.asyncio
async def test_the_queries_of_a_row_are_asked_side_by_side_and_stored_in_query_order() -> None:
    row = CFG.plan["podcasts"]
    now = datetime(2026, 10, 30, tzinfo=UTC)
    queries = [
        q.format(account="Acme", domain="acme.example", year=now.year, prev_year=now.year - 1)
        for q in row.queries
    ]
    # the first query is the slowest: it must still be the first one stored
    provider = QueryProvider({queries[0]: 0.1, queries[1]: 0.07, queries[2]: 0.07})
    agent, meter, payloads = pull_agent(provider)
    started = time.monotonic()
    out = await agent.pull_row(run_ctx(), row, meter, consent_problem=None, internal_history=False)
    elapsed = time.monotonic() - started
    assert elapsed < 0.17, elapsed  # one after another: 0.24 s plus the stores; together: 0.1 s
    assert out.status == "stored"
    assert [q.text for q in out.queries] == queries
    assert out.call_refs == tuple(f"ref-{q}" for q in queries)
    texts = [payloads.items[ref][1].text for ref in out.payload_refs]
    assert texts == queries


@pytest.mark.asyncio
async def test_a_refusal_stops_the_row_where_the_one_at_a_time_loop_stopped() -> None:
    row = CFG.plan["podcasts"]
    now = datetime(2026, 10, 30, tzinfo=UTC)
    queries = [
        q.format(account="Acme", domain="acme.example", year=now.year, prev_year=now.year - 1)
        for q in row.queries
    ]
    # the second query is refused at the credits' limit: the first is kept, the third (already
    # asked, its ledger entry replays when the user raises the cap) is not stored
    provider = QueryProvider(refuse={queries[1]: "cap_reached"})
    agent, meter, _ = pull_agent(provider)
    out = await agent.pull_row(run_ctx(), row, meter, consent_problem=None, internal_history=False)
    assert out.status == "stored"
    assert [q.text for q in out.queries] == [queries[0]]
    assert agent.capped is True
    # an attended run is asked to approve the spend instead
    provider = QueryProvider(refuse={queries[1]: "no_cap"})
    agent, meter, _ = pull_agent(provider)
    attended = run_ctx().__class__(*(*list(vars(run_ctx()).values())[:5], True, "sk-1"))
    with pytest.raises(SpendPause):
        await agent.pull_row(attended, row, meter, consent_problem=None, internal_history=False)


class _Gates:
    """Calls in flight per route, and the highest number seen."""

    def __init__(self) -> None:
        self.now: dict[str, int] = {}
        self.peak: dict[str, int] = {}
        self.started: list[tuple[str, float]] = []

    def enter(self, route: str) -> None:
        self.now[route] = self.now.get(route, 0) + 1
        self.peak[route] = max(self.peak.get(route, 0), self.now[route])
        self.started.append((route, time.monotonic()))

    def leave(self, route: str) -> None:
        self.now[route] -= 1


def scraped(url: str, text: str = "Acme page.") -> Any:
    from account_intelligence.connectors.base import Operation
    from account_intelligence.connectors.models import ScrapedPageRecord

    return ScrapedPageRecord(
        kind="scraped_page",
        provenance=prov(url, Operation.SCRAPE_JOB_PAGE),
        lineage_hint=None,
        requested_url=url,
        final_url=url,
        target_status=200,
        markdown_text=text,
        title_text="t",
        publish_date=None,
    )


def search_of(own: int, off: int, tag: str) -> ConnectorResult:
    records = tuple(page(f"https://acme.example/{tag}/own{i}") for i in range(own))
    records += tuple(page(f"https://other{i}.example/{tag}") for i in range(off))
    return ConnectorResult(status="ok", records=records, call_refs=(f"s-{tag}",))


@pytest.mark.asyncio
async def test_the_page_reads_of_a_search_overlap_and_stay_in_the_searchs_order(
    monkeypatch: Any,
) -> None:
    gates = _Gates()

    async def fake(route: str, args: Any, cc: Any, rt: Any, *, operation: Any = None) -> Any:
        gates.enter(route)
        try:
            await asyncio.sleep(DELAY)
            if route == "open_web_search":
                return search_of(3, 4, "q")
            if route == "job_boards_and_pages":
                return ConnectorResult(
                    status="ok", records=(scraped(args.url),), call_refs=(f"c-{args.url[-6:]}",)
                )
            return ConnectorResult(status="ok", records=(page(args.url),), call_refs=("r",))
        finally:
            gates.leave(route)

    monkeypatch.setenv("RESEARCH_CAPS", "off")  # every page the search found is read
    monkeypatch.setattr(provider_module, "call_need", fake)
    adapter = ConnectorProvider(object(), uuid4(), skill=CFG)  # type: ignore[arg-type]
    started = time.monotonic()
    result = await adapter.call(run_ctx(), "web_search", "acme podcast", "podcasts")
    elapsed = time.monotonic() - started
    # a search, then 3 own reads and 4 other-site reads together: 2 steps, not 1 + 3 + 4
    assert elapsed < 4 * DELAY, elapsed
    assert gates.peak["job_boards_and_pages"] == 3 and gates.peak["read_known_page"] == 4
    urls = [i.url for i in result.items]
    # the search's own results (snippets) first, then the reads in the search's order
    assert urls[:7] == [f"https://acme.example/q/own{i}" for i in range(3)] + [
        f"https://other{i}.example/q" for i in range(4)
    ]
    assert urls[7:] == [f"https://acme.example/q/own{i}" for i in range(3)] + [
        f"https://other{i}.example/q" for i in range(4)
    ]


@pytest.mark.asyncio
async def test_a_research_never_has_more_calls_at_a_provider_than_its_ceiling(
    monkeypatch: Any,
) -> None:
    """Many rows and queries ask for page reads at once; the scrape provider's ceiling
    (config/parallel.yaml) holds, so the governor's bounded wait is never what queues them."""
    from parallel.config import load_parallel_config

    ceiling = load_parallel_config().providers.ceiling("p_scrape")
    gates = _Gates()

    async def fake(route: str, args: Any, cc: Any, rt: Any, *, operation: Any = None) -> Any:
        gates.enter(route)
        try:
            await asyncio.sleep(0.01)
            if route == "open_web_search":
                tag = "".join(c for c in args.queries[0] if c.isalnum()) + str(
                    bool(args.include_domains)
                )
                return search_of(6, 0, tag)
            return ConnectorResult(
                status="ok", records=(scraped(args.url),), call_refs=(f"c-{args.url[-9:]}",)
            )
        finally:
            gates.leave(route)

    monkeypatch.setenv("RESEARCH_CAPS", "off")
    monkeypatch.setattr(provider_module, "call_need", fake)
    adapter = ConnectorProvider(object(), uuid4(), skill=CFG)  # type: ignore[arg-type]
    ctx = run_ctx()
    await asyncio.gather(
        *(
            adapter.call(ctx, "web_search", f"acme earnings call {n}", "earnings_filings")
            for n in range(12)
        )
    )
    assert 1 < gates.peak["job_boards_and_pages"] <= ceiling, gates.peak
    assert gates.peak["open_web_search"] <= load_parallel_config().providers.ceiling("p_search")


@pytest.mark.asyncio
async def test_the_open_search_and_the_own_site_search_of_a_query_run_together(
    monkeypatch: Any,
) -> None:
    gates = _Gates()

    async def fake(route: str, args: Any, cc: Any, rt: Any, *, operation: Any = None) -> Any:
        gates.enter(route)
        try:
            await asyncio.sleep(DELAY)
            if route == "open_web_search":
                return search_of(0, 0, "x")
            return ConnectorResult(status="ok", call_refs=("f",))
        finally:
            gates.leave(route)

    monkeypatch.setattr(provider_module, "call_need", fake)
    adapter = ConnectorProvider(object(), uuid4(), skill=CFG)  # type: ignore[arg-type]
    await adapter.call(run_ctx(), "web_search", "acme overview", "basic_fit_check")
    assert gates.peak["open_web_search"] == 2  # the open search and the own-site search
    gates.peak.clear()
    # a row with a restricted search (the register) asks that one at the same time
    await adapter.call(run_ctx(), "web_search", "acme earnings", "earnings_filings")
    assert gates.peak["open_web_search"] == 3
    gates.peak.clear()
    # a feed row: the feed pages while the newsroom and the newswires are searched
    mark = len(gates.started)
    await adapter.call(run_ctx(), "structured_signals", "acme press release", "company_news")
    assert gates.peak["news_tech_job_feeds"] == 1 and gates.peak["open_web_search"] == 2
    starts = {route: t for route, t in gates.started[mark:]}
    assert abs(starts["news_tech_job_feeds"] - starts["open_web_search"]) < DELAY


@pytest.mark.asyncio
async def test_the_entity_lock_reads_the_home_page_and_searches_the_brand_together(
    monkeypatch: Any,
) -> None:
    gates = _Gates()

    async def fake(route: str, args: Any, cc: Any, rt: Any, *, operation: Any = None) -> Any:
        gates.enter(route)
        try:
            await asyncio.sleep(DELAY)
            if route == "open_web_search":
                return search_of(1, 1, "lock")
            return ConnectorResult(
                status="ok", records=(scraped(args.url, "Acme home page"),), call_refs=("h",)
            )
        finally:
            gates.leave(route)

    monkeypatch.setattr(provider_module, "call_need", fake)
    adapter = ConnectorProvider(object(), uuid4(), skill=CFG)  # type: ignore[arg-type]
    started = time.monotonic()
    lock = await adapter.entity_lock(run_ctx())
    assert time.monotonic() - started < 1.6 * DELAY
    assert lock.confirmed and lock.home_url == "https://acme.example/"

    # a home page that cannot be read is "unreadable", and the brand search still counts
    async def broken(route: str, args: Any, cc: Any, rt: Any, *, operation: Any = None) -> Any:
        from account_intelligence.connectors.errors import ConnectorError

        if route == "job_boards_and_pages":
            raise ConnectorError("unreadable")
        return search_of(1, 1, "lock")

    monkeypatch.setattr(provider_module, "call_need", broken)
    lock = await adapter.entity_lock(run_ctx())
    assert any("unreadable" in p for p in lock.problems)


@pytest.mark.asyncio
async def test_stored_pages_are_read_back_side_by_side(h: Harness) -> None:
    h.grant_consent()
    views = slow(h.payloads, "view", 0.1)
    started = time.monotonic()
    _, state = await h.run("speed-views")
    elapsed = time.monotonic() - started
    assert views[0] >= 5, views[0]
    # read back one at a time the views alone would take calls x 0.1 s
    assert elapsed < views[0] * 0.1 * 0.6, (elapsed, views[0])
    assert state["outcome"] in ("completed", "partial")
