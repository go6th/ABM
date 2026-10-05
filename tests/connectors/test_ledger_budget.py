"""Ledger v2 and budget gate against the real Postgres test database (forced RLS).

Proves: pull-once replay; identical concurrent calls pay once; two workers on one run
never both pay; a stale attempt is reconciled with ``possible_duplicate``; the budget
cap and the 60-call cap hold under concurrency; spend equals the ledger sum; unknown
cost is charged a reserve and flagged, never 0; ledger rows are tenant isolated.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.budget import BudgetDefaults
from account_intelligence.connectors.errors import BudgetExceeded, LeaseUnavailable
from account_intelligence.connectors.ledger import (
    CallKey,
    Claimed,
    InFlight,
    Ledger,
    Outcome,
    Replay,
)
from account_intelligence.connectors.models import FeedArgs, WebSearchArgs
from account_intelligence.connectors.service import call_operation
from account_intelligence.db.session import tenant_txn
from tests.connectors.conftest import (
    FakeClock,
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


def _ledger(clock: FakeClock, ttl: int = 300) -> Ledger:
    return Ledger(tenant_txn, clock, lease_ttl_s=ttl, budget_defaults=BudgetDefaults(5.0, 60))


async def _assert_spend_equals_ledger(rt_ledger: Ledger, ctx) -> None:  # type: ignore[no-untyped-def]
    assert Decimal(run_row(ctx.run_id)["spent_usd"]) == await rt_ledger.ledger_sum(ctx)


# ------------------------------------------------------------------ pull once


async def test_a_done_call_is_replayed_without_calling_the_provider(
    harness: Harness, tenant_a: UUID
) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    harness.providers[ProviderCode.P_SEARCH].script(Reply.cassette("p_search", "web_search"))
    rt = harness.runtime()
    first = await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)
    second = await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)
    assert harness.providers[ProviderCode.P_SEARCH].calls == 1
    assert first.records == second.records  # same stored payload, same records
    assert (first.replayed, second.replayed) == (False, True)
    assert second.charged_usd == 0
    rows = ledger_rows(run)
    assert len(rows) == 1
    row = rows[0]
    assert (row["provider_code"], row["operation"], row["cost_source"]) == (
        "p_search",
        "web_search",
        "documented",
    )
    assert row["cost_usd"] == Decimal("0.005") and row["provider_cost_usd"] == Decimal("0.005")
    assert row["http_status"] == 200 and row["attempt"] == 1 and row["possible_duplicate"] is False
    assert row["response_ref"].startswith(f"tenants/{tenant_a}/accounts/{account}/runs/{run}/raw/")
    assert run_row(run) == {"spent_usd": Decimal("0.005000"), "tool_calls_used": 1}
    await _assert_spend_equals_ledger(rt.ledger, ctx)


async def test_feed_is_pulled_once_per_run_and_a_delta_is_a_new_key(
    harness: Harness, tenant_a: UUID
) -> None:
    from datetime import date

    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    fake = harness.providers[ProviderCode.P_SIGNALS]
    fake.fallback = Reply.cassette("p_signals", "news_events")
    rt = harness.runtime()
    await call_operation(Operation.NEWS_EVENTS, FeedArgs(domain=domain), ctx, rt)
    await call_operation(Operation.NEWS_EVENTS, FeedArgs(domain=domain), ctx, rt)
    assert fake.calls == 1
    await call_operation(
        Operation.NEWS_EVENTS, FeedArgs(domain=domain, since=date(2026, 9, 1)), ctx, rt
    )
    assert fake.calls == 2
    assert fake.requests[-1].url.params["found_at_from"] == "2026-09-01"


# ------------------------------------------------------------------ concurrency


async def test_fifty_concurrent_identical_calls_make_one_provider_call(
    harness: Harness, tenant_a: UUID
) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    fake = harness.providers[ProviderCode.P_SEARCH]
    fake.fallback = Reply.cassette("p_search", "web_search")
    rt = harness.runtime()
    results = await asyncio.gather(
        *[call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt) for _ in range(50)]
    )
    assert fake.calls == 1
    assert len({r.records for r in results}) == 1
    assert run_row(run) == {"spent_usd": Decimal("0.005000"), "tool_calls_used": 1}
    await _assert_spend_equals_ledger(rt.ledger, ctx)


async def test_two_workers_on_one_run_never_both_pay(harness: Harness, tenant_a: UUID) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    fake = harness.providers[ProviderCode.P_SEARCH]
    fake.fallback = Reply.cassette("p_search", "web_search")
    worker_a = harness.runtime(worker_id="worker-a")
    worker_b = harness.runtime(worker_id="worker-b")
    outcomes = await asyncio.gather(
        *[call_operation(Operation.WEB_SEARCH, SEARCH, ctx, w) for w in (worker_a, worker_b) * 5],
        return_exceptions=True,
    )
    paid = [o for o in outcomes if not isinstance(o, BaseException)]
    refused = [o for o in outcomes if isinstance(o, LeaseUnavailable)]
    assert paid and refused and len(paid) + len(refused) == 10
    assert fake.calls == 1
    assert len(ledger_rows(run)) == 1
    started = [e for e in event_rows(run) if e["event"] == "started"]
    assert len(started) == 1 and len({e["owner"] for e in started}) == 1


# The starter's ledgered_call is retired with the starter graph; the same guarantee on the one
# run path is test_fifty_concurrent_identical_calls_make_one_provider_call above.


# ------------------------------------------------------------------ crash reconciliation


async def test_stale_started_attempt_is_reissued_once_and_flagged(tenant_a: UUID) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    clock = FakeClock()
    ledger = _ledger(clock, ttl=60)
    key = CallKey(
        args_hash="a" * 64,
        tool="p_search:web_search",
        provider_code="p_search",
        operation="web_search",
    )
    first = await ledger.claim(ctx, "dead-worker", key, 0.25)
    assert isinstance(first, Claimed) and first.attempt == 1
    # the dead worker never finalises; a second worker is locked out until the lease expires
    with pytest.raises(LeaseUnavailable):
        await ledger.claim(ctx, "worker-b", key, 0.25)
    clock.advance(61)
    second = await ledger.claim(ctx, "worker-b", key, 0.25)
    assert isinstance(second, Claimed)
    assert (second.attempt, second.possible_duplicate, second.lease_epoch) == (2, True, 2)
    await ledger.finalize_done(
        ctx, "worker-b", key, second, Outcome(charged_usd=0.25, response_ref="r", response={})
    )
    replay = await ledger.claim(ctx, "worker-b", key, 0.25)
    assert isinstance(replay, Replay) and replay.possible_duplicate is True
    events = [(e["attempt"], e["event"]) for e in event_rows(run)]
    assert events == [(1, "started"), (1, "abandoned"), (2, "started"), (2, "done")]
    # the abandoned attempt stays charged (the provider may have billed it): 0.25 + 0.25
    assert run_row(run)["spent_usd"] == Decimal("0.500000")
    await _assert_spend_equals_ledger(ledger, ctx)
    # the zombie wakes up and finalises late: recorded, never charged twice
    await ledger.finalize_done(
        ctx, "dead-worker", key, first, Outcome(charged_usd=0.25, response_ref="r", response={})
    )
    assert run_row(run)["spent_usd"] == Decimal("0.500000")
    await _assert_spend_equals_ledger(ledger, ctx)


async def test_same_owner_in_flight_is_waited_for_not_repaid(tenant_a: UUID) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    ledger = _ledger(FakeClock())
    key = CallKey(args_hash="b" * 64, tool="t")
    claimed = await ledger.claim(ctx, "w", key, 0.1)
    assert isinstance(claimed, Claimed)
    assert isinstance(await ledger.claim(ctx, "w", key, 0.1), InFlight)
    await ledger.finalize_failed(
        ctx, "w", key, claimed, Outcome(charged_usd=0.0, error_class="RateLimited")
    )
    retry = await ledger.claim(ctx, "w", key, 0.1)
    assert isinstance(retry, Claimed) and retry.attempt == 2 and retry.possible_duplicate is False
    assert run_row(run)["tool_calls_used"] == 2  # every paid attempt counts toward the cap
    await _assert_spend_equals_ledger(ledger, ctx)


async def test_resumed_async_job_is_polled_not_restarted(tenant_a: UUID) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    clock = FakeClock()
    ledger = _ledger(clock, ttl=10)
    key = CallKey(args_hash="c" * 64, tool="p_org:people_by_technology")
    first = await ledger.claim(ctx, "dead", key, 0.25)
    assert isinstance(first, Claimed)
    await ledger.mark_submitted(ctx, "dead", key, first, "job-123")
    clock.advance(11)
    resumed = await ledger.claim(ctx, "w2", key, 0.25)
    assert isinstance(resumed, Claimed)
    assert resumed.resume_job_id == "job-123" and resumed.possible_duplicate is False
    # the unfinished job was never charged by the provider: its reservation is released
    assert run_row(run)["spent_usd"] == Decimal("0.250000")
    await _assert_spend_equals_ledger(ledger, ctx)


# ------------------------------------------------------------------ budget


async def test_budget_cap_is_never_exceeded_under_concurrency(tenant_a: UUID) -> None:
    account, run, domain = make_run(tenant_a, budget_usd=1.0)
    ctx = context(tenant_a, account, run, domain)
    ledger = _ledger(FakeClock())
    keys = [CallKey(args_hash=f"{i:064x}", tool="t") for i in range(12)]
    outcomes = await asyncio.gather(
        *[ledger.claim(ctx, "w", k, 0.3) for k in keys], return_exceptions=True
    )
    ok = [o for o in outcomes if isinstance(o, Claimed)]
    over = [o for o in outcomes if isinstance(o, BudgetExceeded)]
    assert (len(ok), len(over)) == (3, 9)
    assert run_row(run)["spent_usd"] == Decimal("0.900000") <= Decimal("1.0")
    await _assert_spend_equals_ledger(ledger, ctx)


async def test_the_sixty_first_call_is_refused(tenant_a: UUID) -> None:
    account, run, domain = make_run(tenant_a, budget_usd=100.0)
    ctx = context(tenant_a, account, run, domain)
    ledger = _ledger(FakeClock())
    for i in range(60):
        claim = await ledger.claim(ctx, "w", CallKey(args_hash=f"{i:064x}", tool="t"), 0.01)
        assert isinstance(claim, Claimed)
    with pytest.raises(BudgetExceeded):
        await ledger.claim(ctx, "w", CallKey(args_hash=f"{60:064x}", tool="t"), 0.01)
    assert run_row(run)["tool_calls_used"] == 60


async def test_estimate_is_trued_up_to_the_actual(tenant_a: UUID) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    ledger = _ledger(FakeClock())
    key = CallKey(args_hash="d" * 64, tool="t")
    claim = await ledger.claim(ctx, "w", key, 0.40)
    assert isinstance(claim, Claimed)
    assert run_row(run)["spent_usd"] == Decimal("0.400000")
    await ledger.finalize_done(
        ctx, "w", key, claim, Outcome(charged_usd=0.15, response_ref="r", response={})
    )
    assert run_row(run)["spent_usd"] == Decimal("0.150000")
    await _assert_spend_equals_ledger(ledger, ctx)


async def test_unknown_cost_is_flagged_and_charged_a_reserve_never_zero(
    harness: Harness, tenant_a: UUID
) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    harness.providers[ProviderCode.P_SIGNALS].script(Reply.cassette("p_signals", "job_openings"))
    rt = harness.runtime()
    result = await call_operation(Operation.JOB_OPENINGS, FeedArgs(domain=domain), ctx, rt)
    assert result.cost_usd is None and result.cost_source == "credits_only"
    assert result.charged_usd == rt.config.defaults.unknown_cost_reserve_usd > 0
    row = ledger_rows(run)[0]
    assert row["provider_cost_usd"] is None and row["cost_source"] == "credits_only"
    assert row["credits"] == Decimal("1") and row["cost_usd"] > 0
    await _assert_spend_equals_ledger(rt.ledger, ctx)


async def test_budget_exceeded_stops_before_the_provider_is_called(
    harness: Harness, tenant_a: UUID
) -> None:
    account, run, domain = make_run(tenant_a, budget_usd=0.001)
    ctx = context(tenant_a, account, run, domain)
    rt = harness.runtime()
    with pytest.raises(BudgetExceeded):
        await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)
    assert harness.providers[ProviderCode.P_SEARCH].calls == 0
    assert event_rows(run) == []


# ------------------------------------------------------------------ isolation


async def test_ledger_rows_are_invisible_to_another_tenant(
    harness: Harness, tenant_a: UUID, tenant_b: UUID
) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    harness.providers[ProviderCode.P_SEARCH].script(Reply.cassette("p_search", "web_search"))
    await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, harness.runtime())
    for table in ("tool_calls", "tool_call_events", "run_leases"):
        async with tenant_txn(tenant_b) as s:
            n = (
                await s.execute(text(f"SELECT count(*) FROM {table} WHERE run_id = :r"), {"r": run})
            ).scalar_one()
        assert n == 0, table
        async with tenant_txn(tenant_a) as s:
            n = (
                await s.execute(text(f"SELECT count(*) FROM {table} WHERE run_id = :r"), {"r": run})
            ).scalar_one()
        assert n >= 1, table


async def test_parallel_runs_of_two_tenants_do_not_see_each_other(
    harness: Harness, tenant_a: UUID, tenant_b: UUID
) -> None:
    acc_a, run_a, dom_a = make_run(tenant_a)
    acc_b, run_b, dom_b = make_run(tenant_b)
    fake = harness.providers[ProviderCode.P_SEARCH]
    fake.fallback = Reply.cassette("p_search", "web_search")
    rt = harness.runtime()
    await asyncio.gather(
        call_operation(Operation.WEB_SEARCH, SEARCH, context(tenant_a, acc_a, run_a, dom_a), rt),
        call_operation(Operation.WEB_SEARCH, SEARCH, context(tenant_b, acc_b, run_b, dom_b), rt),
    )
    assert fake.calls == 2  # same args, different runs: each run pulls once
    async with tenant_txn(tenant_a) as s:
        runs = {r[0] for r in (await s.execute(text("SELECT run_id FROM tool_call_events"))).all()}
    assert run_b not in runs and run_a in runs


async def test_app_role_cannot_update_or_delete_ledger_history(tenant_a: UUID) -> None:
    for sql in (
        "UPDATE tool_call_events SET charged_usd = 0",
        "DELETE FROM tool_call_events",
        "DELETE FROM tool_calls",
    ):
        with pytest.raises(Exception, match="permission denied"):
            async with tenant_txn(tenant_a) as s:
                await s.execute(text(sql))
