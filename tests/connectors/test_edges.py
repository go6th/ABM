"""Edge behaviour of the core modules (ledger, budget, ratelimit, paginate, security)."""

from __future__ import annotations

import asyncio
import random
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from account_intelligence.connectors.base import ParsedPage, ProviderCode
from account_intelligence.connectors.budget import BudgetDefaults, RunNotFound, reserve
from account_intelligence.connectors.errors import (
    LeaseUnavailable,
    ProviderJobTimeout,
    RateLimited,
    SecurityError,
)
from account_intelligence.connectors.ledger import CallKey, Claimed, Ledger, Outcome
from account_intelligence.connectors.paginate import PageLimits, PollLimits, poll_job
from account_intelligence.connectors.ratelimit import BucketLimit, RateLimiter
from account_intelligence.connectors.security import (
    Secret,
    UrlPolicy,
    check_final_url,
    check_url_shape,
    system_resolver,
    validate_target_url,
)
from account_intelligence.db.session import tenant_txn
from tests.connectors.conftest import FakeClock, FakeSleep, context, event_rows, make_run, run_row

DEFAULTS = BudgetDefaults(5.0, 60)


# ------------------------------------------------------------------ ledger and budget (database)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_released_lease_lets_the_next_worker_in_immediately(tenant_a: UUID) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    ledger = Ledger(tenant_txn, FakeClock(), lease_ttl_s=300, budget_defaults=DEFAULTS)
    assert await ledger.acquire_lease(ctx, "a") == 1
    assert await ledger.acquire_lease(ctx, "a") == 1  # renewal keeps the epoch
    with pytest.raises(LeaseUnavailable):
        await ledger.acquire_lease(ctx, "b")
    await ledger.release_lease(ctx, "a")
    assert await ledger.acquire_lease(ctx, "b") == 2


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_next_slice_on_the_same_worker_sees_a_cut_off_call_as_abandoned(
    tenant_a: UUID,
) -> None:
    # slice 1's timeout cancelled a call after "started"; slice 2 runs on the same worker
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    ledger = Ledger(tenant_txn, FakeClock(), lease_ttl_s=300, budget_defaults=DEFAULTS)
    key = CallKey(args_hash="f" * 64, tool="t")
    assert await ledger.acquire_lease(ctx, "w") == 1
    cut_off = await ledger.claim(ctx, "w", key, 0.0, charge_budget=False)
    assert isinstance(cut_off, Claimed) and cut_off.lease_epoch == 1
    await ledger.release_lease(ctx, "w")
    assert await ledger.acquire_lease(ctx, "w") == 2  # a lease taken anew is a new epoch
    again = await ledger.claim(ctx, "w", key, 0.0, charge_budget=False)
    assert isinstance(again, Claimed) and again.attempt == 2 and again.possible_duplicate
    assert [e["event"] for e in event_rows(run)] == ["started", "abandoned", "started"]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_unbudgeted_stale_attempt_and_late_failure_of_an_abandoned_attempt(
    tenant_a: UUID,
) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    clock = FakeClock()
    ledger = Ledger(tenant_txn, clock, lease_ttl_s=10, budget_defaults=DEFAULTS)
    key = CallKey(args_hash="e" * 64, tool="t")
    dead = await ledger.claim(ctx, "dead", key, 0.0, charge_budget=False)
    assert isinstance(dead, Claimed)
    clock.advance(11)
    alive = await ledger.claim(ctx, "alive", key, 0.0, charge_budget=False)
    assert isinstance(alive, Claimed) and alive.possible_duplicate
    # the dead worker's call fails late: recorded, no charge moves
    await ledger.finalize_failed(
        ctx, "dead", key, dead, Outcome(charged_usd=0.3, error_class="X"), charge_budget=True
    )
    assert run_row(run)["spent_usd"] == Decimal("0")
    assert [e["event"] for e in event_rows(run)] == ["started", "abandoned", "started", "failed"]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_budget_refuses_negative_estimates_and_unknown_runs(tenant_a: UUID) -> None:
    async with tenant_txn(tenant_a) as s:
        with pytest.raises(ValueError):
            await reserve(s, uuid4(), -1.0, DEFAULTS)
        with pytest.raises(RunNotFound):
            await reserve(s, uuid4(), 0.1, DEFAULTS)


# ------------------------------------------------------------------ rate limiter


def test_bucket_limits_are_validated() -> None:
    for rate, burst in [(0, 1), (-1, 1), (1, 0)]:
        with pytest.raises(ValueError):
            BucketLimit(rate, burst)


@pytest.mark.asyncio
async def test_pacing_loop_is_bounded_even_with_small_waits() -> None:
    class AlwaysWait:
        async def take(self, key: str, limit: BucketLimit, now: float) -> float:
            return 0.1

        async def set_cooldown(self, key: str, until: float) -> None: ...

        async def cooldown_until(self, key: str) -> float:
            return 0.0

    clock = FakeClock()
    limiter = RateLimiter(
        AlwaysWait(),
        {ProviderCode.P_ORG: BucketLimit(1, 1)},
        clock,
        FakeSleep(clock),
        max_wait_s=100,
        max_iterations=5,
    )
    with pytest.raises(RateLimited, match="loop cap"):
        await limiter.acquire(ProviderCode.P_ORG)


# ------------------------------------------------------------------ pagination and jobs


def test_page_limits_are_validated() -> None:
    with pytest.raises(ValueError):
        PageLimits(0, 1, 1)


def test_job_poll_returns_the_page_respects_the_deadline_and_times_out_without_cancel() -> None:
    clock = FakeClock()
    sleep = FakeSleep(clock)
    done = ParsedPage((), 0, None)
    answers = iter([None, None, done])

    async def poll() -> ParsedPage | None:
        return next(answers)

    page = asyncio.run(
        poll_job(poll, PollLimits(3, 0, 10, 100), clock, sleep, random.Random(1), deadline=None)
    )
    assert page is done and sleep.calls == [3, 3]

    async def never() -> ParsedPage | None:
        return None

    deadline = clock.monotonic() + 7
    with pytest.raises(ProviderJobTimeout):
        asyncio.run(
            poll_job(
                never, PollLimits(3, 0, 10, 100), clock, sleep, random.Random(1), deadline=deadline
            )
        )
    assert clock.monotonic() < deadline


# ------------------------------------------------------------------ security


@pytest.mark.parametrize(
    "url",
    [
        "https:///nohost",
        "https://acme.com:99999/",
        "https://[::ffff:10.0.0.1]/",  # IPv4-mapped IPv6 to a private address
        "https://[fe80::1%25eth0]/",  # link-local with a zone id
    ],
)
def test_more_shape_refusals(url: str) -> None:
    with pytest.raises(SecurityError):
        check_url_shape(url)


@pytest.mark.asyncio
async def test_public_ip_literals_pass_without_dns_and_dns_failures_refuse() -> None:
    async def boom(host: str) -> list[str]:
        raise OSError("no such host")

    async def slow(host: str) -> list[str]:
        await asyncio.sleep(10)
        return ["93.184.215.14"]

    await validate_target_url("https://93.184.215.14/", UrlPolicy(resolver=boom))
    with pytest.raises(SecurityError, match="resolve"):
        await validate_target_url("https://acme.com/", UrlPolicy(resolver=boom))
    with pytest.raises(SecurityError, match="resolve"):
        await validate_target_url(
            "https://acme.com/", UrlPolicy(resolver=slow, resolve_timeout_s=0.01)
        )


@pytest.mark.asyncio
async def test_system_resolver_resolves_without_network_for_localhost() -> None:
    addresses = await system_resolver("localhost")
    assert addresses and all(a in ("127.0.0.1", "::1") for a in addresses)
    with pytest.raises(SecurityError):
        await validate_target_url("https://localhost/", UrlPolicy())


def test_final_url_unchanged_or_absent_is_accepted_and_secret_must_be_non_empty() -> None:
    check_final_url("https://a.acme.com/x", None, "acme.com", ())
    check_final_url("https://a.acme.com/x", "https://a.acme.com/x", "acme.com", ())
    with pytest.raises(ValueError):
        Secret("")


def test_job_poll_stops_at_max_polls() -> None:
    clock = FakeClock()
    sleep = FakeSleep(clock)
    polls: list[int] = []

    async def never() -> ParsedPage | None:
        polls.append(1)
        return None

    with pytest.raises(ProviderJobTimeout):
        asyncio.run(
            poll_job(
                never, PollLimits(1, 0, 2, 1000), clock, sleep, random.Random(1), deadline=None
            )
        )
    assert len(polls) == 2


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_lease_its_owner_stopped_renewing_can_be_taken_before_its_ttl_runs_out(
    tenant_a: UUID,
) -> None:
    """Orchestration review F5: a worker killed mid-slice leaves a live lease (the ttl is longer
    than a slice) that blocked the retries of its slice. A lease its owner has not renewed for the
    stale time can be taken; one the owner keeps renewing cannot, and the epoch moves on."""
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    clock = FakeClock()
    ledger = Ledger(
        tenant_txn, clock, lease_ttl_s=1800, budget_defaults=DEFAULTS, lease_stale_after_s=90
    )
    assert await ledger.acquire_lease(ctx, "a") == 1
    clock.advance(60)
    with pytest.raises(LeaseUnavailable):  # a renewed 60 s ago: alive
        await ledger.acquire_lease(ctx, "b")
    assert await ledger.acquire_lease(ctx, "a") == 1  # a's heartbeat: the epoch is kept
    clock.advance(60)
    with pytest.raises(LeaseUnavailable):  # 60 s since the last beat: still alive
        await ledger.acquire_lease(ctx, "b")
    clock.advance(40)  # 100 s of silence: a is gone
    assert await ledger.acquire_lease(ctx, "b") == 2  # the epoch moved on
    with pytest.raises(LeaseUnavailable):  # and the dead owner cannot take it back while b beats
        await ledger.acquire_lease(ctx, "a")


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_without_a_stale_time_only_an_expired_lease_can_be_taken(tenant_a: UUID) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    clock = FakeClock()
    ledger = Ledger(tenant_txn, clock, lease_ttl_s=300, budget_defaults=DEFAULTS)
    assert await ledger.acquire_lease(ctx, "a") == 1
    clock.advance(200)
    with pytest.raises(LeaseUnavailable):
        await ledger.acquire_lease(ctx, "b")
    clock.advance(101)
    assert await ledger.acquire_lease(ctx, "b") == 2
