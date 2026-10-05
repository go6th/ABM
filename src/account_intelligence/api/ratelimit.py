"""Configurable token-bucket rate limiter primitives.

Guarantees: each key is isolated and denied calls receive a bounded retry
duration (GUARANTEE(test_token_bucket_is_scoped_and_reports_retry_time)).
Limits must be supplied by reviewed configuration; this module invents no values.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from threading import Lock
from typing import Protocol

from account_intelligence.api.errors import APIProblem


class RateLimitPort(Protocol):
    """Limiter contract so callers stay independent of the bucket implementation."""

    def consume(self, key: str, cost: float = 1) -> tuple[bool, int]:
        """Charge cost against the key; return (allowed, seconds until retry)."""
        ...


class TokenBucketLimiter:
    """Single-process token bucket for conservative fallback or deterministic tests."""

    def __init__(
        self,
        *,
        capacity: float,
        refill_per_second: float,
        max_buckets: int,
        clock: Callable[[], float],
    ) -> None:
        if capacity <= 0 or refill_per_second <= 0 or max_buckets <= 0:
            raise ValueError("token-bucket limits must be positive")
        self._capacity = capacity
        self._refill_rate = refill_per_second
        self._max_buckets = max_buckets
        self._clock = clock
        self._buckets: OrderedDict[str, tuple[float, float, float]] = OrderedDict()
        self._lock = Lock()

    def consume(self, key: str, cost: float = 1) -> tuple[bool, int]:
        """Consume cost tokens; return whether allowed and seconds until retry."""
        if not key or cost <= 0 or cost > self._capacity:
            raise ValueError("rate-limit key and cost must be valid")
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self._max_buckets:
                    self._remove_refilled_buckets(now)
                    if len(self._buckets) >= self._max_buckets:
                        return False, max(1, int(self._capacity / self._refill_rate))
                tokens, last_refill, _ = self._capacity, now, now
            else:
                tokens, last_refill, _ = bucket
                tokens = min(
                    self._capacity,
                    tokens + max(0.0, now - last_refill) * self._refill_rate,
                )
            if tokens < cost:
                retry_after = max(1, int((cost - tokens) / self._refill_rate + 0.999))
                self._buckets[key] = (tokens, now, now)
                self._buckets.move_to_end(key)
                return False, retry_after
            self._buckets[key] = (tokens - cost, now, now)
            self._buckets.move_to_end(key)
            return True, 0

    def _remove_refilled_buckets(self, now: float) -> None:
        stale = [
            key
            for key, (tokens, _, last_seen) in self._buckets.items()
            if tokens >= self._capacity or now - last_seen >= self._capacity / self._refill_rate
        ]
        for key in stale:
            self._buckets.pop(key, None)


def enforce_rate_limit(allowed: bool, retry_after_seconds: int) -> None:
    """Raise a typed 429 with Retry-After when a bucket denies the request."""
    if not allowed:
        raise APIProblem(
            429,
            "rate_limited",
            "Too many requests",
            "The request rate limit was exceeded.",
            headers={"Retry-After": str(max(1, retry_after_seconds))},
        )
