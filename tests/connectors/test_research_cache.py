"""Research cache: a finished call of an earlier run of the SAME tenant is adopted by a later run
at no cost; never across tenants, never past its TTL, never for a fresh run; a stale in-flight
attempt by this worker is re-issued instead of ending the run ``CallInFlight``."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.cache import (
    CACHE_COST_SOURCE,
    InMemoryCacheIndex,
    ResearchCache,
    ResearchCacheConfig,
    load_research_cache_config,
)
from account_intelligence.connectors.errors import CallInFlight
from account_intelligence.connectors.ledger import CallKey, Claimed, InFlight
from account_intelligence.connectors.models import WebSearchArgs
from account_intelligence.connectors.service import call_operation
from tests.connectors.conftest import Harness, Reply, context, ledger_rows, make_run, run_row

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio(loop_scope="session")]

SEARCH = WebSearchArgs(queries=("acme funding round",))


def _cached_runtime(harness: Harness, cache: ResearchCache):  # type: ignore[no-untyped-def]
    rt = harness.runtime()
    ledger = rt.ledger
    ledger.cache = cache
    return rt


def _run_for(tenant: UUID, account: UUID) -> UUID:
    """A second run of the same tenant and account (the research cache is keyed by tenant)."""
    from uuid import uuid4

    import psycopg

    from tests.connectors.conftest import admin_dsn

    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        return c.execute(
            "INSERT INTO account_runs (tenant_id, account_id, graph_version, budget_json,"
            " reserved_credits, idempotency_key, budget_usd, cap_required)"
            " VALUES (%s, %s, 'test', '{}', 1, %s, 5.0, false) RETURNING id",
            (tenant, account, "k-" + uuid4().hex),
        ).fetchone()[0]


def _cache(**over: object) -> ResearchCache:
    base = load_research_cache_config().model_dump()
    base.update(over)
    return ResearchCache(ResearchCacheConfig.model_validate(base), InMemoryCacheIndex())


async def test_the_config_file_validates_and_gives_ttls() -> None:
    cfg = load_research_cache_config()
    assert cfg.enabled and cfg.ttl_for("web_search") == timedelta(days=7)
    assert cfg.ttl_for("never_seen") == timedelta(days=cfg.default_ttl_days)
    off = ResearchCacheConfig.model_validate({**cfg.model_dump(), "enabled": False})
    assert off.ttl_for("web_search") is None
    zero = ResearchCacheConfig.model_validate(
        {**cfg.model_dump(), "ttl_days_by_operation": {"web_search": 0}}
    )
    assert zero.ttl_for("web_search") is None
    raw = yaml.safe_load((Path("config") / "research_cache.yaml").read_text())
    assert set(raw["ttl_days_by_operation"]) <= {o.value for o in Operation}


async def test_a_second_run_reuses_a_fresh_call_of_the_same_tenant_at_no_cost(
    harness: Harness, tenant_a: UUID
) -> None:
    cache = _cache()
    account, run1, domain = make_run(tenant_a)
    rt = _cached_runtime(harness, cache)
    harness.providers[ProviderCode.P_SEARCH].script(Reply.cassette("p_search", "web_search"))
    first = await call_operation(
        Operation.WEB_SEARCH, SEARCH, context(tenant_a, account, run1, domain), rt
    )
    assert harness.providers[ProviderCode.P_SEARCH].calls == 1

    run2 = _run_for(tenant_a, account)
    ctx2 = context(tenant_a, account, run2, domain)
    second = await call_operation(Operation.WEB_SEARCH, SEARCH, ctx2, rt)
    assert harness.providers[ProviderCode.P_SEARCH].calls == 1  # no provider call
    assert second.records == first.records and second.replayed
    assert second.charged_usd == 0.0 and second.cost_source == CACHE_COST_SOURCE
    rows = ledger_rows(run2)
    assert len(rows) == 1 and rows[0]["cost_source"] == CACHE_COST_SOURCE
    assert Decimal(rows[0]["cost_usd"]) == 0
    assert rows[0]["response_ref"] == ledger_rows(run1)[0]["response_ref"]
    assert Decimal(run_row(run2)["spent_usd"]) == 0
    # the same call again in run 2 replays its own row (pull once still holds)
    third = await call_operation(Operation.WEB_SEARCH, SEARCH, ctx2, rt)
    assert third.replayed and harness.providers[ProviderCode.P_SEARCH].calls == 1


async def test_the_cache_never_crosses_tenants_or_a_fresh_run(
    harness: Harness, tenant_a: UUID, tenant_b: UUID
) -> None:
    cache = _cache()
    rt = _cached_runtime(harness, cache)
    harness.providers[ProviderCode.P_SEARCH].script(
        Reply.cassette("p_search", "web_search"),
        Reply.cassette("p_search", "web_search"),
        Reply.cassette("p_search", "web_search"),
    )
    account_a, run_a, domain = make_run(tenant_a)
    await call_operation(
        Operation.WEB_SEARCH, SEARCH, context(tenant_a, account_a, run_a, domain), rt
    )
    account_b, run_b, domain_b = make_run(tenant_b, domain)
    await call_operation(
        Operation.WEB_SEARCH, SEARCH, context(tenant_b, account_b, run_b, domain_b), rt
    )
    assert harness.providers[ProviderCode.P_SEARCH].calls == 2  # tenant B paid its own call
    fresh_run = _run_for(tenant_a, account_a)
    fresh = replace(context(tenant_a, account_a, fresh_run, domain), reuse_cached=False)
    result = await call_operation(Operation.WEB_SEARCH, SEARCH, fresh, rt)
    assert harness.providers[ProviderCode.P_SEARCH].calls == 3 and not result.replayed


async def test_a_call_past_its_ttl_is_paid_again(harness: Harness, tenant_a: UUID) -> None:
    cache = _cache(ttl_days_by_operation={"web_search": 1})
    rt = _cached_runtime(harness, cache)
    harness.providers[ProviderCode.P_SEARCH].script(
        Reply.cassette("p_search", "web_search"), Reply.cassette("p_search", "web_search")
    )
    account, run1, domain = make_run(tenant_a)
    await call_operation(Operation.WEB_SEARCH, SEARCH, context(tenant_a, account, run1, domain), rt)
    # the stored row carries the database clock; age it by two days (admin connection: the
    # application role may only insert ledger rows)
    import psycopg

    from tests.connectors.conftest import admin_dsn

    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        # the ledger's freshness clock is the runtime clock (the fake one here), so age the row
        # relative to it
        c.execute(
            "UPDATE tool_calls SET created_at = %s WHERE run_id = %s",
            (harness.clock.now() - timedelta(days=2), run1),
        )
    run2 = _run_for(tenant_a, account)
    result = await call_operation(
        Operation.WEB_SEARCH, SEARCH, context(tenant_a, account, run2, domain), rt
    )
    assert harness.providers[ProviderCode.P_SEARCH].calls == 2 and not result.replayed


async def test_a_failed_call_is_never_reused(harness: Harness, tenant_a: UUID) -> None:
    """A row whose response carries ``error_class`` (a paid body we refused) is not a hit."""
    cache = _cache()
    account, run1, domain = make_run(tenant_a)
    ctx1 = context(tenant_a, account, run1, domain)
    key = CallKey(
        args_hash="h" * 64,
        tool="p_search:web_search",
        provider_code="p_search",
        operation="web_search",
        route="open_web_search",
    )
    rt = _cached_runtime(harness, cache)
    claim = await rt.ledger.claim(ctx1, rt.worker_id, key, 0.005)
    assert isinstance(claim, Claimed)
    from account_intelligence.connectors.ledger import Outcome

    await rt.ledger.finalize_done(
        ctx1,
        rt.worker_id,
        key,
        claim,
        Outcome(
            0.005,
            response={"error_class": "SchemaDriftError", "args": {}},
            response_ref="tenants/x/raw/a.json.gz",
        ),
    )
    run2 = _run_for(tenant_a, account)
    ctx2 = context(tenant_a, account, run2, domain)
    again = await rt.ledger.claim(ctx2, rt.worker_id, key, 0.005)
    assert isinstance(again, Claimed)  # a new paid attempt, not a replay of the failure


async def test_a_stale_in_flight_attempt_of_this_worker_is_reissued(
    harness: Harness, tenant_a: UUID
) -> None:
    """A worker restart keeps its lease and owner id: its abandoned ``started`` event used to read
    as in flight until ``CallInFlight`` failed the run (2026-10-12)."""
    rt = harness.runtime()
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    key = CallKey(
        args_hash="s" * 64,
        tool="p_search:web_search",
        provider_code="p_search",
        operation="web_search",
    )
    first = await rt.ledger.claim(ctx, rt.worker_id, key, 0.005)
    assert isinstance(first, Claimed)
    # still fresh: the identical claim waits (in flight)
    assert isinstance(await rt.ledger.claim(ctx, rt.worker_id, key, 0.005), InFlight)
    # the attempt's start is stamped by the database clock and this test moves only the fake
    # clock, so the abandoned attempt is reached past both bounds: staleness and the lease
    # (1500 s since slices became 1200 s, 2026-10-20; it was 300 s, under the 900 s staleness,
    # which is what this step relied on)
    harness.clock.advance(max(rt.ledger.in_flight_stale_s, rt.ledger.lease_ttl_s) + 1)
    again = await rt.ledger.claim(ctx, rt.worker_id, key, 0.005)
    assert isinstance(again, Claimed) and again.possible_duplicate and again.attempt == 2
    with pytest.raises(CallInFlight):
        raise CallInFlight(attempt=1)  # the class stays a retryable provider error
