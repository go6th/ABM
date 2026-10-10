"""Shared rate limits for first-party authentication.

Cloud deployments use the same TLS Redis service as the connector governor, so the IP and
account buckets apply across API instances. Dev and test use a bounded in-process bucket.
Keys contain hashes rather than submitted email addresses or source IPs.
"""

from __future__ import annotations

import hashlib
import math
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from fastapi import Request

from account_intelligence.api.problem import DependencyUnavailable
from account_intelligence.api.ratelimit import TokenBucketLimiter, enforce_rate_limit
from account_intelligence.config import CONFIG_DIR, get_settings
from account_intelligence.connectors.ratelimit import BucketLimit, RedisRateStore


@lru_cache(maxsize=1)
def _limits() -> dict[str, tuple[float, float]]:
    raw: Any = yaml.safe_load((Path(CONFIG_DIR) / "auth.yaml").read_text())
    values = raw.get("rate_limits") if isinstance(raw, dict) else None
    if not isinstance(values, dict):
        raise ValueError("config/auth.yaml must define auth rate limits")
    result: dict[str, tuple[float, float]] = {}
    for key, capacity_name, refill_name in (
        ("ip", "ip_capacity", "ip_refill_per_second"),
        ("account", "account_capacity", "account_refill_per_second"),
    ):
        capacity, refill = values.get(capacity_name), values.get(refill_name)
        if (
            not isinstance(capacity, (int, float))
            or isinstance(capacity, bool)
            or capacity < 1
            or not isinstance(refill, (int, float))
            or isinstance(refill, bool)
            or refill <= 0
        ):
            raise ValueError("auth token-bucket limits must be positive")
        result[key] = (float(capacity), float(refill))
    return result


_memory: dict[str, TokenBucketLimiter] = {}
_redis_client: Any = None


def _memory_limiter(kind: str) -> TokenBucketLimiter:
    if kind not in _memory:
        limits = _limits()
        capacity, refill = limits[kind]
        _memory[kind] = TokenBucketLimiter(
            capacity=capacity,
            refill_per_second=refill,
            max_buckets=100_000,
            clock=time.monotonic,
        )
    return _memory[kind]


def _redis_store() -> RedisRateStore:
    global _redis_client
    settings = get_settings()
    if not settings.redis_host:
        raise DependencyUnavailable()
    try:
        if _redis_client is None:
            import redis.asyncio as redis

            _redis_client = redis.Redis(
                host=settings.redis_host,
                port=6379,
                ssl=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                health_check_interval=30,
            )
        return RedisRateStore(_redis_client, prefix="identity:ratelimit:")
    except Exception as error:
        raise DependencyUnavailable() from error


async def consume(request: Request, *, account: str, action: str) -> None:
    """Apply account and source-address buckets; deny auth when shared Redis is unhealthy."""
    limits = _limits()
    address = request.client.host if request.client is not None else "unknown"
    ip_key = hashlib.sha256(address.encode()).hexdigest()
    account_key = hashlib.sha256(account.strip().lower().encode()).hexdigest()
    settings = get_settings()
    waits: list[float] = []
    for kind, value in (("ip", ip_key), ("account", account_key)):
        if settings.redis_host and kind == "ip":
            # The API is reached through the private web BFF, so its TCP peer is the web service,
            # not the browser. Cloud Armor applies the per-client IP auth bucket at the public
            # edge; Redis owns the account bucket across API instances.
            continue
        capacity, refill = limits[kind]
        key = f"{action}:{kind}:{value}"
        if settings.redis_host:
            try:
                wait = await _redis_store().take(
                    key, BucketLimit(rate_per_s=refill, burst=capacity), time.time()
                )
            except Exception as error:
                raise DependencyUnavailable() from error
            waits.append(wait)
        else:
            allowed, retry_after = _memory_limiter(kind).consume(key)
            waits.append(0 if allowed else float(retry_after))
    blocked = [wait for wait in waits if wait > 0]
    enforce_rate_limit(not blocked, math.ceil(max(blocked, default=0)))


async def close() -> None:
    """Close the process-shared Redis connection at ASGI shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
