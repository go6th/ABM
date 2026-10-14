"""Shared rate limits for first-party authentication and per-user money and compute actions.

Cloud deployments use the same TLS Redis service as the connector governor, so the IP and
account buckets apply across API instances. Dev and test use a bounded in-process bucket.
Keys contain hashes rather than submitted email addresses or source IPs. The per-user buckets
(``consume_user``, values in ``config/rate_limits.yaml``, security audit M4) key on the verified
client and user only.

GUARANTEE(test_per_user_buckets_refuse_past_capacity_per_user_and_action)
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
    if not settings.redis_url:
        raise DependencyUnavailable()
    try:
        if _redis_client is None:
            import redis.asyncio as redis

            _redis_client = redis.Redis.from_url(
                settings.redis_url,
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
        if settings.redis_url and kind == "ip":
            # The API is reached through the private web BFF, so its TCP peer is the web service,
            # not the browser. Cloud Armor applies the per-client IP auth bucket at the public
            # edge; Redis owns the account bucket across API instances.
            continue
        capacity, refill = limits[kind]
        key = f"{action}:{kind}:{value}"
        if settings.redis_url:
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


@lru_cache(maxsize=1)
def user_limits() -> dict[str, tuple[float, float]]:
    """Each per-user action's (capacity, refill per second) from ``config/rate_limits.yaml``."""
    raw: Any = yaml.safe_load((Path(CONFIG_DIR) / "rate_limits.yaml").read_text())
    values = raw.get("user_buckets") if isinstance(raw, dict) else None
    if not isinstance(values, dict) or not values:
        raise ValueError("config/rate_limits.yaml must define user_buckets")
    result: dict[str, tuple[float, float]] = {}
    for action, spec in values.items():
        capacity = spec.get("capacity") if isinstance(spec, dict) else None
        refill = spec.get("refill_per_second") if isinstance(spec, dict) else None
        if (
            not isinstance(capacity, (int, float))
            or isinstance(capacity, bool)
            or capacity < 1
            or not isinstance(refill, (int, float))
            or isinstance(refill, bool)
            or refill <= 0
        ):
            raise ValueError(f"user bucket {action!r} must have a positive capacity and refill")
        result[str(action)] = (float(capacity), float(refill))
    return result


_user_memory: dict[str, TokenBucketLimiter] = {}


async def consume_user(tenant_id: object, user_id: str, action: str) -> None:
    """Take one token from the user's bucket for ``action``: 429 with Retry-After when empty,
    503 when the shared Redis is configured but unhealthy (fail closed, as sign-in does)."""
    capacity, refill = user_limits()[action]
    subject = hashlib.sha256(f"{tenant_id}:{user_id}".encode()).hexdigest()
    key = f"user:{action}:{subject}"
    if get_settings().redis_url:
        try:
            wait = await _redis_store().take(
                key, BucketLimit(rate_per_s=refill, burst=capacity), time.time()
            )
        except Exception as error:
            raise DependencyUnavailable() from error
        enforce_rate_limit(wait <= 0, math.ceil(wait))
        return
    if action not in _user_memory:
        _user_memory[action] = TokenBucketLimiter(
            capacity=capacity, refill_per_second=refill, max_buckets=100_000, clock=time.monotonic
        )
    enforce_rate_limit(*_user_memory[action].consume(key))


async def close() -> None:
    """Close the process-shared Redis connection at ASGI shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
