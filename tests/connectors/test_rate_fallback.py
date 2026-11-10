"""SD-07: losing Redis must not stop every paid call.

The provider rate limiter shared its buckets through Redis with no fallback, so a Redis error
failed every paid call platform-wide. ``FallbackRateStore`` keeps using Redis while it answers
and, on any error, paces through this process's own bucket at a quarter of the provider's rate
and a burst of one (conservative: several instances may each fall back at once), logging the
``rate_limit_degraded`` event the existing alert pages on. It goes back to Redis as soon as Redis
answers again.

GUARANTEE(test_a_redis_outage_paces_paid_calls_in_process_instead_of_failing_them)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from account_intelligence.connectors.base import ProviderCode
from account_intelligence.connectors.ratelimit import (
    FALLBACK_RATE_SHARE,
    BucketLimit,
    FallbackRateStore,
    InMemoryRateStore,
    RateLimiter,
)


class _Down:
    """A Redis store whose every call fails, as during a Memorystore outage."""

    def __init__(self) -> None:
        self.calls = 0
        self.up = False
        self.inner = InMemoryRateStore()

    async def take(self, key: str, limit: BucketLimit, now: float) -> float:
        self.calls += 1
        if not self.up:
            raise ConnectionError("redis down")
        return await self.inner.take(key, limit, now)

    async def set_cooldown(self, key: str, until: float) -> None:
        self.calls += 1
        if not self.up:
            raise ConnectionError("redis down")
        await self.inner.set_cooldown(key, until)

    async def cooldown_until(self, key: str) -> float:
        self.calls += 1
        if not self.up:
            raise ConnectionError("redis down")
        return await self.inner.cooldown_until(key)


class _Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.t


@pytest.mark.asyncio
async def test_a_redis_outage_paces_paid_calls_in_process_instead_of_failing_them(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _Clock()

    async def sleep(seconds: float) -> None:
        clock.t += timedelta(seconds=seconds)

    redis = _Down()
    limit = BucketLimit(rate_per_s=4.0, burst=10.0)
    limiter = RateLimiter(
        FallbackRateStore(redis), {ProviderCode.P_SEARCH: limit}, clock, sleep, max_wait_s=10.0
    )
    with caplog.at_level(logging.WARNING):
        first = await limiter.acquire(ProviderCode.P_SEARCH)
        second = await limiter.acquire(ProviderCode.P_SEARCH)
        await limiter.note_retry_after(ProviderCode.P_SEARCH, 2.0)  # a 429 is still honoured
        third = await limiter.acquire(ProviderCode.P_SEARCH)
    assert first == 0.0  # granted: the call goes ahead instead of failing
    # conservative: burst one, a quarter of the rate (1 call/s here), so the second call waits 1 s
    assert FALLBACK_RATE_SHARE == 0.25
    assert second == pytest.approx(1.0)
    assert third == pytest.approx(2.0)  # the provider's cooldown holds in the fallback too
    degraded = [r for r in caplog.records if r.getMessage() == "rate_limit_degraded"]
    assert len(degraded) == 1  # logged once when Redis is lost, not once per call
    # Redis is back: the shared store is used again (the fallback no longer paces)
    redis.up = True
    before = redis.calls
    await limiter.acquire(ProviderCode.P_SEARCH)
    assert redis.calls > before
