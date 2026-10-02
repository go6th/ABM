"""Per-provider token bucket with a shared cooldown.

Pacing is the connector's job; the retry decision is the node's. ``acquire`` waits at
most ``max_wait_s`` (bounded loop) and otherwise raises ``RateLimited`` so the node
policy owns the retry. A 429 or ``Retry-After`` sets the provider-wide cooldown, which
every worker sharing the store honours. Stores are injected: in-memory for a single
process and tests, Redis for workers on several pods. Redis holds no tenant data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from account_intelligence.connectors.base import Clock, ProviderCode, Sleep
from account_intelligence.connectors.errors import RateLimited


@dataclass(frozen=True)
class BucketLimit:
    rate_per_s: float  # sustained tokens per second
    burst: float  # bucket capacity

    def __post_init__(self) -> None:
        if self.rate_per_s <= 0 or self.burst < 1:
            raise ValueError("rate must be positive and burst at least 1")


class RateStore(Protocol):
    async def take(self, key: str, limit: BucketLimit, now: float) -> float:
        """Atomically take one token. Return 0.0 when granted, else seconds until one is."""
        ...

    async def set_cooldown(self, key: str, until: float) -> None:
        """Extend (never shorten) the cooldown deadline for ``key``."""
        ...

    async def cooldown_until(self, key: str) -> float: ...


class InMemoryRateStore:
    """Single-process store. Instance state only; one instance per runtime."""

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, float]] = {}
        self._cooldowns: dict[str, float] = {}

    async def take(self, key: str, limit: BucketLimit, now: float) -> float:
        tokens, last = self._buckets.get(key, (limit.burst, now))
        if now > last:
            tokens = min(limit.burst, tokens + (now - last) * limit.rate_per_s)
            last = now
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, last)
            return 0.0
        self._buckets[key] = (tokens, last)
        return (1.0 - tokens) / limit.rate_per_s

    async def set_cooldown(self, key: str, until: float) -> None:
        self._cooldowns[key] = max(self._cooldowns.get(key, 0.0), until)

    async def cooldown_until(self, key: str) -> float:
        return self._cooldowns.get(key, 0.0)


_TAKE_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local state = redis.call('HMGET', key, 'tokens', 'last')
local tokens = tonumber(state[1])
local last = tonumber(state[2])
if tokens == nil then tokens = burst; last = now end
if now > last then
  tokens = math.min(burst, tokens + (now - last) * rate)
  last = now
end
local wait = 0
if tokens >= 1 then tokens = tokens - 1 else wait = (1 - tokens) / rate end
redis.call('HSET', key, 'tokens', tostring(tokens), 'last', tostring(last))
redis.call('PEXPIRE', key, ttl)
return tostring(wait)
"""

_COOLDOWN_LUA = """
local cur = tonumber(redis.call('GET', KEYS[1]) or '0')
local new = tonumber(ARGV[1])
if new > cur then redis.call('SET', KEYS[1], tostring(new), 'PX', ARGV[2]) end
return 1
"""


class RedisRateStore:
    """Shared store for workers on several pods (``redis.asyncio`` client injected)."""

    def __init__(self, client: object, prefix: str = "connectors:ratelimit:") -> None:
        self._client = client
        self._prefix = prefix

    async def take(self, key: str, limit: BucketLimit, now: float) -> float:
        ttl_ms = int(max(1.0, limit.burst / limit.rate_per_s) * 2000) + 1000
        raw = await self._client.eval(  # type: ignore[attr-defined]
            _TAKE_LUA, 1, self._prefix + "bucket:" + key, limit.rate_per_s, limit.burst, now, ttl_ms
        )
        return float(raw)

    async def set_cooldown(self, key: str, until: float) -> None:
        ttl_ms = 3_600_000
        await self._client.eval(  # type: ignore[attr-defined]
            _COOLDOWN_LUA, 1, self._prefix + "cooldown:" + key, until, ttl_ms
        )

    async def cooldown_until(self, key: str) -> float:
        raw = await self._client.get(self._prefix + "cooldown:" + key)  # type: ignore[attr-defined]
        return float(raw) if raw is not None else 0.0


class RateLimiter:
    def __init__(
        self,
        store: RateStore,
        limits: Mapping[ProviderCode, BucketLimit],
        clock: Clock,
        sleep: Sleep,
        *,
        max_wait_s: float = 10.0,
        max_cooldown_s: float = 60.0,
        default_cooldown_s: float = 5.0,
        max_iterations: int = 20,
    ) -> None:
        self._store = store
        self._limits = dict(limits)
        self._clock = clock
        self._sleep = sleep
        self.max_wait_s = max_wait_s
        self.max_cooldown_s = max_cooldown_s
        self.default_cooldown_s = default_cooldown_s
        self.max_iterations = max_iterations

    def _now(self) -> float:
        return self._clock.now().timestamp()

    async def acquire(self, provider: ProviderCode) -> float:
        """Wait for a token; return seconds waited. Raise RateLimited past ``max_wait_s``."""
        limit = self._limits[provider]
        waited = 0.0
        for _ in range(self.max_iterations):
            now = self._now()
            cooldown = await self._store.cooldown_until(provider) - now
            wait = cooldown if cooldown > 0 else await self._store.take(provider, limit, now)
            if wait <= 0:
                return waited
            if waited + wait > self.max_wait_s:
                raise RateLimited(
                    "local pacing wait over cap", retry_after=wait, provider_code=provider
                )
            await self._sleep(wait)
            waited += wait
        raise RateLimited("pacing loop cap reached", retry_after=None, provider_code=provider)

    async def note_retry_after(self, provider: ProviderCode, retry_after: float | None) -> float:
        """Set the provider-wide cooldown after a 429; return the honoured (capped) delay."""
        delay = self.default_cooldown_s if retry_after is None or retry_after < 0 else retry_after
        delay = min(delay, self.max_cooldown_s)
        await self._store.set_cooldown(provider, self._now() + delay)
        return delay


def parse_retry_after(value: str | None) -> float | None:
    """Seconds form only (both sourced providers document integer seconds)."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None
