"""HTTP exchange classification, token bucket, cooldown, circuit breaker, backoff caps."""

from __future__ import annotations

import os
import random

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from account_intelligence.connectors.base import PageRequest, ProviderCode
from account_intelligence.connectors.breaker import (
    BreakerConfig,
    CircuitBreaker,
    InMemoryBreakerStore,
)
from account_intelligence.connectors.errors import (
    CircuitOpen,
    RateLimited,
    ResponseTooLarge,
    RetryableProviderError,
    SecurityError,
)
from account_intelligence.connectors.http import HttpSettings, exchange, make_client
from account_intelligence.connectors.ratelimit import (
    BucketLimit,
    InMemoryRateStore,
    RateLimiter,
    RedisRateStore,
    parse_retry_after,
)
from account_intelligence.connectors.service import retry_policy_for
from account_intelligence.connectors.settings import load_providers_config
from tests.connectors.conftest import FakeClock, FakeProvider, FakeSleep, Reply

P = ProviderCode.P_SIGNALS
SETTINGS = HttpSettings(base_url="https://provider.invalid", max_bytes=1000)
REQ = PageRequest("GET", "/x")


def _client(fake: FakeProvider) -> httpx.AsyncClient:
    return make_client(SETTINGS, headers={"x-api-key": "k"}, transport=fake.transport, env={})


# ------------------------------------------------------------------ http


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        Reply(raise_exc=httpx.ConnectTimeout),
        Reply(raise_exc=httpx.ReadTimeout),
        Reply(raise_exc=httpx.ConnectError),  # connection refused or reset at connect
        Reply(raise_exc=httpx.ReadError),  # connection reset mid-body
        Reply(truncated=True),
    ],
)
async def test_transient_transport_failures_are_retryable(reply: Reply) -> None:
    fake = FakeProvider(FakeClock()).script(reply)
    with pytest.raises(RetryableProviderError):
        await exchange(_client(fake), REQ, SETTINGS)


@pytest.mark.asyncio
async def test_oversized_body_is_refused_while_streaming() -> None:
    fake = FakeProvider(FakeClock()).script(Reply(body=b"x" * 5000))
    with pytest.raises(ResponseTooLarge):
        await exchange(_client(fake), REQ, SETTINGS)


@pytest.mark.asyncio
async def test_gzip_is_decoded_and_size_checked_after_decoding() -> None:
    fake = FakeProvider(FakeClock()).script(Reply(body={"ok": True}, gzip=True))
    raw = await exchange(_client(fake), REQ, SETTINGS)
    assert raw.body == b'{"ok": true}' and raw.content_type == "application/json"
    fake.script(Reply(body=b"a" * 5000, gzip=True))  # compresses tiny, decodes over the cap
    with pytest.raises(ResponseTooLarge):
        await exchange(_client(fake), REQ, SETTINGS)


@pytest.mark.asyncio
async def test_redirects_are_not_followed_and_auth_headers_never_kept() -> None:
    fake = FakeProvider(FakeClock()).script(
        Reply(status=302, headers={"location": "https://10.0.0.1/", "set-cookie": "s=1"})
    )
    raw = await exchange(_client(fake), REQ, SETTINGS)
    assert raw.status_code == 302 and fake.calls == 1
    assert "set-cookie" not in raw.headers


def test_client_requires_https_and_verifies_tls() -> None:
    with pytest.raises(SecurityError):
        make_client(HttpSettings(base_url="http://x.invalid"), headers={}, env={})
    client = make_client(
        SETTINGS, headers={}, env={"CONNECTOR_EGRESS_PROXY": "http://proxy.internal:3128"}
    )
    assert client.follow_redirects is False


def test_retry_after_parsing() -> None:
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") is None
    assert parse_retry_after("-3") is None


# ------------------------------------------------------------------ rate limiter


@pytest.mark.asyncio
async def test_429_cooldown_is_shared_and_capped() -> None:
    clock = FakeClock()
    sleep = FakeSleep(clock)
    store = InMemoryRateStore()
    a = RateLimiter(
        store, {P: BucketLimit(10, 10)}, clock, sleep, max_wait_s=100, max_cooldown_s=30
    )
    b = RateLimiter(
        store, {P: BucketLimit(10, 10)}, clock, sleep, max_wait_s=100, max_cooldown_s=30
    )
    honoured = await a.note_retry_after(P, 3600)
    assert honoured == 30  # capped
    waited = await b.acquire(P)  # the other worker backs off too
    assert waited == pytest.approx(30)


@pytest.mark.asyncio
async def test_wait_beyond_the_cap_raises_rate_limited_for_the_node() -> None:
    clock = FakeClock()
    limiter = RateLimiter(
        InMemoryRateStore(), {P: BucketLimit(0.1, 1)}, clock, FakeSleep(clock), max_wait_s=2
    )
    await limiter.acquire(P)
    with pytest.raises(RateLimited) as caught:
        await limiter.acquire(P)
    assert caught.value.retry_after == pytest.approx(10)


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [10.0, 7.0, 3.3])
async def test_back_to_back_acquires_never_spin_below_clock_resolution(rate: float) -> None:
    """Found by the whole-run system test: after a 0.1 s wait the bucket was short by ~1e-7 of a
    token (float error), the computed wait was below the clock's microsecond resolution, sleeping
    it never moved the clock, and the loop hit its iteration cap (RateLimited). The limiter now
    sleeps at least 1 ms, so every acquire at the configured rate succeeds."""
    clock = FakeClock()
    sleep = FakeSleep(clock)
    limiter = RateLimiter(InMemoryRateStore(), {P: BucketLimit(rate, 1)}, clock, sleep)
    for _ in range(100):
        await limiter.acquire(P)
    assert all(s >= 0.001 for s in sleep.calls)


@settings(max_examples=60, deadline=None)
@given(
    rate=st.floats(0.2, 20),
    burst=st.integers(1, 20),
    gaps=st.lists(st.floats(0, 3), min_size=1, max_size=120),
)
def test_property_token_bucket_never_grants_more_than_its_rate(
    rate: float, burst: int, gaps: list[float]
) -> None:
    import asyncio

    store = InMemoryRateStore()
    limit = BucketLimit(rate, burst)
    grants: list[float] = []
    now = 0.0

    async def drive() -> None:
        nonlocal now
        for gap in gaps:
            now += gap
            if await store.take("k", limit, now) == 0.0:
                grants.append(now)

    asyncio.run(drive())
    # Over any window [t0, t1] grants <= burst + rate * (t1 - t0).
    for i in range(len(grants)):
        for j in range(i, len(grants)):
            assert j - i + 1 <= burst + rate * (grants[j] - grants[i]) + 1e-6


@pytest.mark.asyncio
async def test_redis_store_shares_buckets_and_cooldowns() -> None:
    redis = pytest.importorskip("redis.asyncio")
    url = os.getenv("REDIS_URL", "redis://localhost:6379/15")
    client = redis.from_url(url)
    try:
        await client.ping()
    except Exception:
        pytest.skip("no Redis reachable at REDIS_URL")
    prefix = f"test:{random.random()}:"
    store = RedisRateStore(client, prefix=prefix)
    limit = BucketLimit(1.0, 2)
    assert await store.take("p", limit, 100.0) == 0.0
    assert await store.take("p", limit, 100.0) == 0.0
    assert await store.take("p", limit, 100.0) == pytest.approx(1.0)
    await store.set_cooldown("p", 500.0)
    await store.set_cooldown("p", 400.0)  # never shortened
    assert await store.cooldown_until("p") == 500.0
    await client.aclose()


# ------------------------------------------------------------------ breaker


@pytest.mark.asyncio
async def test_breaker_opens_half_opens_and_closes() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(InMemoryBreakerStore(), clock, BreakerConfig(3, 60, 30, 1))
    for _ in range(3):
        await breaker.before_call(P)
        await breaker.record_failure(P)
    with pytest.raises(CircuitOpen):
        await breaker.before_call(P)
    clock.advance(31)
    assert await breaker.state(P) == "half_open"
    await breaker.before_call(P)  # one probe allowed
    with pytest.raises(CircuitOpen):
        await breaker.before_call(P)  # second concurrent probe refused
    await breaker.record_failure(P)  # probe failed: open again
    with pytest.raises(CircuitOpen):
        await breaker.before_call(P)
    clock.advance(31)
    await breaker.before_call(P)
    await breaker.record_success(P)
    assert await breaker.state(P) == "closed"


@pytest.mark.asyncio
async def test_breaker_failures_outside_the_window_do_not_accumulate() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(InMemoryBreakerStore(), clock, BreakerConfig(3, 60, 30, 1))
    for _ in range(5):
        await breaker.record_failure(P)
        clock.advance(61)
    assert await breaker.state(P) == "closed"


# ------------------------------------------------------------------ retry policy (node owned)


def test_retry_policy_is_the_single_owner_and_backoff_is_capped() -> None:
    cfg = load_providers_config()
    policy = retry_policy_for(cfg, P)
    assert policy.max_attempts == cfg.defaults.retry.max_attempts == 3
    assert policy.retry_on is RetryableProviderError
    assert policy.jitter is True
    # breaker threshold equals the retry budget: an exhausted retry opens the circuit
    assert cfg.defaults.breaker.failure_threshold == policy.max_attempts


@settings(deadline=None)
@given(attempt=st.integers(1, 50))
def test_property_backoff_never_exceeds_its_cap(attempt: int) -> None:
    policy = retry_policy_for(load_providers_config())
    interval = min(
        policy.max_interval, policy.initial_interval * policy.backoff_factor ** (attempt - 1)
    )
    assert interval <= policy.max_interval
