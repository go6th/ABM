"""call_operation end to end against the scripted fake provider server and real Postgres."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from decimal import Decimal
from uuid import UUID

import httpx
import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.errors import (
    AuthError,
    CircuitOpen,
    CreditsExhausted,
    NonRetryableProviderError,
    NotSupportedYet,
    RateLimited,
    ResponseTooLarge,
    RetryableProviderError,
    SchemaDriftError,
    SecurityError,
    UnexpectedContentType,
)
from account_intelligence.connectors.http import HttpSettings
from account_intelligence.connectors.models import (
    FeedArgs,
    OrgResolveArgs,
    PeopleByTechnologyArgs,
    PeopleByTitleArgs,
    ReadPageArgs,
    ScrapeJobPageArgs,
    WebSearchArgs,
)
from account_intelligence.connectors.paginate import PageLimits
from account_intelligence.connectors.service import call_operation
from tests.connectors.conftest import (
    CASSETTES,
    Harness,
    Reply,
    context,
    event_rows,
    ledger_rows,
    make_run,
    run_row,
)

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

SEARCH = WebSearchArgs(queries=("acme funding round",))


def _setup(harness: Harness, tenant: UUID, **kw):  # type: ignore[no-untyped-def]
    account, run, domain = make_run(tenant, **kw)
    return context(tenant, account, run, domain), harness.runtime()


def _body(code: str, name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((CASSETTES / code / f"{name}.json").read_text())["response"]["body"]


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (
            Reply(status=429, headers={"retry-after": "7"}, body={"error": {"message": "x"}}),
            RateLimited,
        ),
        (Reply(status=429, body={}), RateLimited),
        (Reply(status=500, body={}), RetryableProviderError),
        (Reply(status=503, body={}), RetryableProviderError),
        (Reply(status=408, body={}), RetryableProviderError),
        (Reply(raise_exc=httpx.ReadTimeout), RetryableProviderError),
        (Reply(raise_exc=httpx.ConnectError), RetryableProviderError),
        (Reply(truncated=True), RetryableProviderError),
        (Reply(status=401, body={}), AuthError),
        (Reply(status=403, body={}), AuthError),
        (Reply(status=402, body={}), CreditsExhausted),
        (Reply(status=422, body={}), NonRetryableProviderError),
        (Reply(status=200, body=b"<html>", content_type="text/html"), UnexpectedContentType),
    ],
)
async def test_failures_are_classified_finalised_and_not_charged(
    harness: Harness, tenant_a: UUID, reply: Reply, error: type[Exception]
) -> None:
    ctx, rt = _setup(harness, tenant_a)
    harness.providers[ProviderCode.P_SEARCH].script(reply)
    with pytest.raises(error) as caught:
        await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)
    assert getattr(caught.value, "attempt", None) == 1  # the node compares this with its budget
    events = [(e["event"], e["error_class"]) for e in event_rows(ctx.run_id)]
    assert events == [("started", None), ("failed", type(caught.value).__name__)]
    assert ledger_rows(ctx.run_id) == []  # nothing to replay: the next attempt calls again
    assert run_row(ctx.run_id) == {"spent_usd": Decimal("0"), "tool_calls_used": 1}
    assert Decimal(run_row(ctx.run_id)["spent_usd"]) == await rt.ledger.ledger_sum(ctx)


async def test_429_sets_the_shared_cooldown_with_the_provider_value(
    harness: Harness, tenant_a: UUID
) -> None:
    ctx, rt = _setup(harness, tenant_a)
    harness.providers[ProviderCode.P_SEARCH].script(
        Reply(status=429, headers={"retry-after": "7"}, body={}),
        Reply.cassette("p_search", "web_search"),
    )
    with pytest.raises(RateLimited) as caught:
        await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)
    assert caught.value.retry_after == 7
    # the node retries: the connector paces first (fake sleep), then the call goes through
    result = await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)
    assert result.status == "done" and pytest.approx(7) == harness.sleep.calls[-1]
    assert [e["attempt"] for e in event_rows(ctx.run_id) if e["event"] == "started"] == [1, 2]


async def test_schema_drift_is_paid_stored_and_replays_as_drift(
    harness: Harness, tenant_a: UUID
) -> None:
    ctx, rt = _setup(harness, tenant_a)
    body = _body("p_search", "web_search")
    body["results"][0].pop("url")
    harness.providers[ProviderCode.P_SEARCH].script(Reply(body=body))
    with pytest.raises(SchemaDriftError) as caught:
        await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)
    assert caught.value.raw_ref in harness.payloads.objects  # raw kept for debugging
    assert len(ledger_rows(ctx.run_id)) == 1  # paid, so recorded
    with pytest.raises(SchemaDriftError):
        await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)  # replay: same verdict
    assert harness.providers[ProviderCode.P_SEARCH].calls == 1


async def test_oversized_body_is_refused(harness: Harness, tenant_a: UUID) -> None:
    ctx, rt = _setup(harness, tenant_a)
    small = {
        ProviderCode.P_SEARCH: replace(rt.http[ProviderCode.P_SEARCH], max_bytes=100),
        **{k: v for k, v in rt.http.items() if k is not ProviderCode.P_SEARCH},
    }
    rt = replace(rt, http=small)
    harness.providers[ProviderCode.P_SEARCH].script(Reply(body={"x": "y" * 500}))
    with pytest.raises(ResponseTooLarge):
        await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)


async def test_circuit_opens_after_the_retry_budget_and_replays_still_work(
    harness: Harness, tenant_a: UUID
) -> None:
    ctx, rt = _setup(harness, tenant_a)
    fake = harness.providers[ProviderCode.P_SEARCH]
    fake.script(Reply.cassette("p_search", "web_search"))
    await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)  # stored for replay
    other = WebSearchArgs(queries=("acme layoffs",))
    fake.fallback = Reply(status=503, body={})
    for _ in range(3):
        with pytest.raises(RetryableProviderError):
            await call_operation(Operation.WEB_SEARCH, other, ctx, rt)
    with pytest.raises(CircuitOpen):
        await call_operation(Operation.WEB_SEARCH, other, ctx, rt)
    calls = fake.calls
    replay = await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)  # no provider call
    assert replay.replayed and fake.calls == calls


async def test_empty_result_is_done_with_zero_counts(harness: Harness, tenant_a: UUID) -> None:
    ctx, rt = _setup(harness, tenant_a)
    harness.providers[ProviderCode.P_SEARCH].script(
        Reply(body={"search_id": "s", "results": [], "session_id": "x"})
    )
    result = await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)
    assert (result.status, result.counts.read, result.counts.matched, result.counts.pages) == (
        "done",
        0,
        0,
        1,
    )


# ------------------------------------------------------------------ pagination through the ledger


def _news_page(ids: list[str], count: int) -> dict:  # type: ignore[type-arg]
    body = copy.deepcopy(_body("p_signals", "news_events"))
    template = body["data"][0]
    body["data"] = [{**copy.deepcopy(template), "id": i} for i in ids]
    body["meta"]["count"] = count
    return body


async def test_signals_feed_pages_until_count_and_each_page_is_a_ledger_key(
    harness: Harness, tenant_a: UUID
) -> None:
    ctx, rt = _setup(harness, tenant_a)
    rt = replace(
        rt,
        connectors={
            **rt.connectors,
            ProviderCode.P_SIGNALS: type(rt.connectors[ProviderCode.P_SIGNALS])(page_limit=2),
        },
    )
    fake = harness.providers[ProviderCode.P_SIGNALS]
    fake.script(
        Reply(body=_news_page(["a", "b"], 5)),
        Reply(body=_news_page(["c", "b"], 5)),
        Reply(body=_news_page(["e"], 5)),
    )
    result = await call_operation(
        Operation.NEWS_EVENTS, FeedArgs(domain=ctx.account_domain), ctx, rt
    )
    assert result.status == "done" and result.stop_reason == "exhausted"
    assert [r.dedupe_key() for r in result.records] == ["a", "b", "c", "e"]
    assert (result.counts.read, result.counts.matched, result.counts.pages) == (5, 4, 3)
    assert [r.url.params["page"] for r in fake.requests] == ["1", "2", "3"]
    assert len(ledger_rows(ctx.run_id)) == 3
    again = await call_operation(
        Operation.NEWS_EVENTS, FeedArgs(domain=ctx.account_domain), ctx, rt
    )
    assert again.replayed and again.records == result.records and fake.calls == 3


async def test_duplicate_page_and_endless_feeds_end_partial_not_done(
    harness: Harness, tenant_a: UUID
) -> None:
    ctx, rt = _setup(harness, tenant_a)
    rt = replace(
        rt,
        connectors={
            **rt.connectors,
            ProviderCode.P_SIGNALS: type(rt.connectors[ProviderCode.P_SIGNALS])(page_limit=1),
        },
        page_limits_override=PageLimits(max_pages=4, max_items=100, max_bytes=10_000_000),
    )
    fake = harness.providers[ProviderCode.P_SIGNALS]
    same = _news_page(["a"], 99)
    fake.script(Reply(body=same), Reply(body=same))
    dup = await call_operation(Operation.NEWS_EVENTS, FeedArgs(domain=ctx.account_domain), ctx, rt)
    assert (dup.status, dup.stop_reason) == ("partial", "loop_detected")
    counter = iter(range(1000))
    fake.fallback = lambda request: Reply(body=_news_page([f"n{next(counter)}"], 10_000))
    endless = await call_operation(
        Operation.NEWS_EVENTS, FeedArgs(domain="other-" + ctx.account_domain), ctx, rt
    )
    assert (endless.status, endless.stop_reason, endless.counts.pages) == (
        "partial",
        "max_pages",
        4,
    )


async def test_slow_pages_stop_at_the_slice_deadline(harness: Harness, tenant_a: UUID) -> None:
    ctx, rt = _setup(harness, tenant_a)
    ctx = replace(ctx, deadline=harness.clock.monotonic() + 100)
    rt = replace(
        rt,
        connectors={
            **rt.connectors,
            ProviderCode.P_SIGNALS: type(rt.connectors[ProviderCode.P_SIGNALS])(page_limit=1),
        },
    )
    fake = harness.providers[ProviderCode.P_SIGNALS]
    fake.script(
        Reply(body=_news_page(["a"], 10), advance_s=60),
        Reply(body=_news_page(["b"], 10), advance_s=60),
    )
    result = await call_operation(
        Operation.NEWS_EVENTS, FeedArgs(domain=ctx.account_domain), ctx, rt
    )
    assert (result.status, result.stop_reason, result.counts.pages) == ("partial", "deadline", 2)


# ------------------------------------------------------------------ provider specifics


async def test_org_people_async_job_starts_once_polls_and_charges_on_success(
    harness: Harness, tenant_a: UUID
) -> None:
    ctx, rt = _setup(harness, tenant_a)
    fake = harness.providers[ProviderCode.P_ORG]
    succeeded = _body("p_org", "people_poll_succeeded")
    succeeded["total"] = 1
    pending = {
        "credits_used": 0,
        "credits_remaining": 5,
        "request_id": "019d...",
        "status": "running",
    }
    fake.script(Reply.cassette("p_org", "people_start"), Reply(body=pending), Reply(body=succeeded))
    args = PeopleByTechnologyArgs(organization_id=1726684, technology_slug="kubernetes")
    result = await call_operation(Operation.PEOPLE_BY_TECHNOLOGY, args, ctx, rt)
    assert result.status == "done" and len(result.records) == 1 and result.credits == 8
    bodies = fake.bodies()
    assert "filter" in bodies[0] and bodies[1] == bodies[2] == {"request_id": "019d..."}
    events = [e["event"] for e in event_rows(ctx.run_id)]
    assert events == ["started", "submitted", "done"]
    assert ledger_rows(ctx.run_id)[0]["credits"] == Decimal("8")
    assert all(3.0 <= s <= 4.0 for s in harness.sleep.calls)  # bounded poll interval with jitter


async def test_org_resolve_request_never_asks_for_a_hosted_file(
    harness: Harness, tenant_a: UUID
) -> None:
    domain = _body("p_org", "org_resolve")["organizations"][0]["input"]["url"]
    ctx, rt = _setup(harness, tenant_a, domain=domain)
    fake = harness.providers[ProviderCode.P_ORG]
    fake.script(Reply.cassette("p_org", "org_resolve"))
    result = await call_operation(Operation.ORG_RESOLVE, OrgResolveArgs(domain=domain), ctx, rt)
    assert "file_name" not in fake.bodies()[0]
    assert result.records[0].organization_id == 1726684  # type: ignore[attr-defined]


async def test_scrape_redirect_off_the_allow_list_is_refused_after_payment(
    harness: Harness, tenant_a: UUID
) -> None:
    ctx, rt = _setup(harness, tenant_a, domain="acme.com")
    body = _body("p_scrape", "scrape_job_page")
    body["data"]["metadata"]["url"] = "https://evil.example.net/phish"
    harness.providers[ProviderCode.P_SCRAPE].script(Reply(body=body))
    args = ScrapeJobPageArgs(url="https://jobs.acme.com/123", page_kind="job_page")
    with pytest.raises(SecurityError):
        await call_operation(Operation.SCRAPE_JOB_PAGE, args, ctx, rt)
    assert len(ledger_rows(ctx.run_id)) == 1  # the provider billed it


async def test_page_reads_are_refused_before_any_spend(harness: Harness, tenant_a: UUID) -> None:
    ctx, rt = _setup(harness, tenant_a, domain="acme.com")
    harness.resolver_table["internal.acme.com"] = ["10.0.0.7"]
    for url in ("https://other.com/careers", "https://internal.acme.com/"):
        with pytest.raises(SecurityError):
            await call_operation(Operation.READ_PAGE, ReadPageArgs(url=url), ctx, rt)
    assert harness.providers[ProviderCode.P_SEARCH].calls == 0 and event_rows(ctx.run_id) == []


async def test_unsupported_operations_and_expensive_scrape_modes(
    harness: Harness, tenant_a: UUID
) -> None:
    ctx, rt = _setup(harness, tenant_a, domain="acme.com")
    with pytest.raises(NotSupportedYet, match="grid"):
        await call_operation(
            Operation.PEOPLE_BY_TITLE,
            PeopleByTitleArgs(domain="acme.com", titles=("CTO",)),
            ctx,
            rt,
        )
    args = ScrapeJobPageArgs(
        url="https://jobs.acme.com/1",
        page_kind="job_page",
        mode="search_with_scrape",
        expensive_mode_reason="twelve months of postings needed",
    )
    with pytest.raises(NotSupportedYet):
        await call_operation(Operation.SCRAPE_JOB_PAGE, args, ctx, rt)
    with pytest.raises(SecurityError):
        await call_operation(
            Operation.WEB_SEARCH, FeedArgs(domain="acme.com"), ctx, rt
        )  # wrong arg type
    assert event_rows(ctx.run_id) == []


async def test_credentials_are_sent_but_never_stored(harness: Harness, tenant_a: UUID) -> None:
    ctx, rt = _setup(harness, tenant_a)
    fake = harness.providers[ProviderCode.P_SEARCH]
    fake.script(Reply.cassette("p_search", "web_search"))
    await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)
    assert fake.requests[0].headers["x-api-key"] == "test-key-search-000"
    stored = json.dumps([ledger_rows(ctx.run_id), event_rows(ctx.run_id)], default=str)
    assert "test-key-search-000" not in stored
    assert all(b"test-key" not in v for v in harness.payloads.objects.values())


async def test_http_settings_follow_the_sourced_timeouts() -> None:
    from account_intelligence.connectors.factory import http_settings
    from account_intelligence.connectors.settings import load_providers_config

    cfg = load_providers_config()
    org = http_settings(cfg.provider(ProviderCode.P_ORG))
    assert isinstance(org, HttpSettings) and org.read_s >= 65  # api-overview.md: 65 s or more
    scrape = http_settings(cfg.provider(ProviderCode.P_SCRAPE))
    assert scrape.read_s > 60  # scrape timeout default 60 000 ms
