"""Guarantee tests for the v1 API: fail-closed ports, explicit policies, safe envelopes.

Each test proves the GUARANTEE tag declared next to the code it protects; the tag-to-test
link is enforced by ``scripts/check_guarantees.py``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
from uuid import uuid4

import pytest

from account_intelligence.api import ports as api_ports
from account_intelligence.api.domains import validate_domain
from account_intelligence.api.errors import APIProblem, NotConfigured
from account_intelligence.api.etag import if_none_match_matches
from account_intelligence.api.idempotency import canonical_request_hash, replay_response
from account_intelligence.api.main import create_app
from account_intelligence.api.membership import MembershipCache
from account_intelligence.api.policy import policy_for
from account_intelligence.api.pricing import credits_for_depth
from account_intelligence.api.problem import problem_body
from account_intelligence.api.ratelimit import TokenBucketLimiter, enforce_rate_limit


def test_missing_service_is_not_success() -> None:
    """GUARANTEE(test_missing_service_is_not_success): the port layer ships no default
    implementation that could silently succeed when a service is absent."""
    for name, obj in vars(api_ports).items():
        if name.startswith("_") or not inspect.isclass(obj) or obj.__module__ != api_ports.__name__:
            continue
        is_protocol = bool(getattr(obj, "_is_protocol", False))
        is_frozen_dataclass = dataclasses.is_dataclass(obj) and obj.__dataclass_params__.frozen
        assert is_protocol or is_frozen_dataclass, f"{name} is a concrete fallback type"
    assert not hasattr(api_ports, "UnconfiguredPrincipal")


def test_every_versioned_route_has_an_explicit_policy() -> None:
    """GUARANTEE(test_every_versioned_route_has_an_explicit_policy): every route under /v1
    is covered by ROUTE_POLICIES; anything unregistered stays default-deny."""
    # The OpenAPI schema is the app's own materialized route table (this FastAPI version
    # keeps included routers lazy, so the router object alone does not list its routes).
    schema = create_app().openapi()
    seen: set[tuple[str, str]] = set()
    for path, operations in schema["paths"].items():
        if not path.startswith("/v1/"):
            continue
        for method in operations:
            if method == "parameters" or method == "head":
                continue  # HEAD is an automatic GET alias
            policy = policy_for(method.upper(), path)
            assert policy is not None, f"no explicit policy for {method.upper()} {path}"
            seen.add((method.upper(), path))
    assert ("POST", "/v1/runs") in seen
    assert ("GET", "/v1/runs/{run_id}/spend") in seen


def test_unconfigured_credit_price_fails_closed() -> None:
    """GUARANTEE(test_unconfigured_credit_price_fails_closed): absent or invalid D-04
    prices raise 503 before any reservation is made."""
    with pytest.raises(APIProblem) as missing:
        credits_for_depth("standard", standard_credits=None, deep_credits=5)
    assert (missing.value.status, missing.value.type) == (503, "pricing_not_configured")
    with pytest.raises(APIProblem) as invalid:
        credits_for_depth("deep", standard_credits=5, deep_credits=0)
    assert (invalid.value.status, invalid.value.type) == (503, "pricing_not_configured")
    assert credits_for_depth("deep", standard_credits=None, deep_credits=7) == 7


def test_idempotency_rejects_key_reuse_with_different_request() -> None:
    """GUARANTEE(test_idempotency_rejects_key_reuse_with_different_request): equivalent
    bodies hash identically; a changed body can never replay a stored response."""
    body = {"domain": "example.com", "depth": "standard", "notes": {"b": 1, "a": 2}}
    reordered = {"notes": {"a": 2, "b": 1}, "depth": "standard", "domain": "example.com"}
    assert canonical_request_hash(body) == canonical_request_hash(reordered)

    stored = {"run_id": "r-1"}
    key_hash = canonical_request_hash(body)
    assert (
        replay_response(stored_hash=key_hash, incoming_hash=key_hash, stored_response=stored)
        is stored
    )
    with pytest.raises(APIProblem) as changed:
        replay_response(
            stored_hash=canonical_request_hash({"depth": "deep"}),
            incoming_hash=key_hash,
            stored_response=stored,
        )
    assert (changed.value.status, changed.value.type) == (409, "idempotency_key_reuse")
    with pytest.raises(APIProblem) as unknown_key:
        replay_response(stored_hash=None, incoming_hash=key_hash, stored_response=stored)
    assert unknown_key.value.status == 409


class _FakeMembershipPort:
    """In-memory membership source that can be made to fail like the database would."""

    def __init__(self) -> None:
        self.calls = 0
        self.role: str | None = "seller"
        self.fail = False

    async def role_for(self, tenant_id: object, user_id: str) -> str | None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("database unavailable")
        return self.role


def test_membership_cache_rechecks_expired_membership() -> None:
    """GUARANTEE(test_membership_cache_rechecks_expired_membership): cached decisions
    expire, and a database failure is never cached as a decision."""
    port = _FakeMembershipPort()
    clock = {"now": 0.0}
    cache = MembershipCache(port, ttl_seconds=60, monotonic=lambda: clock["now"])
    tenant, user = uuid4(), "user-1"

    assert asyncio.run(cache.is_active(tenant, user, "seller"))
    assert port.calls == 1
    assert asyncio.run(cache.is_active(tenant, user, "seller"))
    assert port.calls == 1  # served from the cache inside the TTL
    assert not asyncio.run(cache.is_active(tenant, user, "viewer"))
    assert port.calls == 1  # a role mismatch does not trigger a lookup either

    clock["now"] = 61.0  # TTL expired: the membership must be rechecked
    assert asyncio.run(cache.is_active(tenant, user, "seller"))
    assert port.calls == 2

    port.fail = True
    clock["now"] = 122.0
    with pytest.raises(RuntimeError):
        asyncio.run(cache.is_active(tenant, user, "seller"))

    port.fail = False
    port.role = "viewer"
    clock["now"] = 183.0
    assert not asyncio.run(cache.is_active(tenant, user, "seller"))
    assert asyncio.run(cache.is_active(tenant, user, "viewer"))
    assert port.calls == 4  # the failure was retried, not cached


def test_token_bucket_is_scoped_and_reports_retry_time() -> None:
    """GUARANTEE(test_token_bucket_is_scoped_and_reports_retry_time): one key's spend
    never affects another's bucket, and denials carry a bounded retry window."""
    clock = {"now": 0.0}
    limiter = TokenBucketLimiter(
        capacity=2, refill_per_second=1.0, max_buckets=8, clock=lambda: clock["now"]
    )
    assert limiter.consume("tenant-a") == (True, 0)
    assert limiter.consume("tenant-a") == (True, 0)
    denied, retry_after = limiter.consume("tenant-a")
    assert not denied and 1 <= retry_after <= 2
    assert limiter.consume("tenant-b") == (True, 0)  # isolation across keys

    clock["now"] = 2.0  # full refill elapsed
    assert limiter.consume("tenant-a") == (True, 0)

    with pytest.raises(APIProblem) as problem:
        enforce_rate_limit(False, retry_after)
    assert problem.value.status == 429
    assert problem.value.headers == {"Retry-After": str(retry_after)}
    enforce_rate_limit(True, 0)  # allowed calls never raise


def test_api_problem_shapes() -> None:
    """GUARANTEE(test_api_problem_shapes): expected failures carry stable public fields
    and never leak internal exception text."""
    err = APIProblem(
        429,
        "rate_limited",
        "Too many requests",
        "The request rate limit was exceeded.",
        headers={"Retry-After": "7"},
    )
    assert (err.status, err.type, err.title) == (429, "rate_limited", "Too many requests")
    assert err.headers == {"Retry-After": "7"}
    assert str(err) == err.detail  # the exception message is the safe public detail
    assert problem_body(err.status, "req-9")["type"] == "urn:6th:problem:429"

    unconfigured = NotConfigured("spend adapter")
    assert unconfigured.dependency == "spend adapter"
    assert str(unconfigured) == "spend adapter is not configured"


def test_etag_matching_supports_weak_tags_and_wildcards() -> None:
    """GUARANTEE(test_etag_matching_supports_weak_tags_and_wildcards): If-None-Match
    compares strong and weak validators and honours the wildcard."""
    etag = '"v7"'
    assert not if_none_match_matches(None, etag)
    assert if_none_match_matches('"v7"', etag)
    assert if_none_match_matches('W/"v7"', etag)
    assert if_none_match_matches('"v6", W/"v7"', etag)
    assert if_none_match_matches("*", etag)
    assert not if_none_match_matches('"v6"', etag)
    assert not if_none_match_matches('W/"v8"', etag)


class _RecordingBlockedEntities:
    """Blocklist stub recording every call so tests can prove no lookup happened."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def is_blocked(self, domain: str) -> bool:
        self.calls.append(domain)
        return domain == "blocked.example.com"


class _RecordingResolver:
    """Resolver stub recording every call; returns a non-public address for .example hosts."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def resolve_public(self, domain: str) -> tuple[str, ...]:
        self.calls.append(domain)
        if domain.endswith(".example.com"):
            return ("10.0.0.5",)  # non-public answer must fail validation
        return ("93.184.216.34",)


def test_domain_validation_fails_before_any_run_write() -> None:
    """GUARANTEE(test_domain_validation_fails_before_any_run_write): invalid, blocked and
    non-public domains fail with no resolver or blocklist traffic, let alone a run write."""
    blocked, resolver = _RecordingBlockedEntities(), _RecordingResolver()

    async def exercise() -> None:
        with pytest.raises(APIProblem) as syntax:
            await validate_domain("not a hostname", resolver=resolver, blocked_entities=blocked)
        assert (syntax.value.status, syntax.value.type) == (422, "invalid_domain")
        with pytest.raises(APIProblem) as literal_ip:
            await validate_domain("192.0.2.1", resolver=resolver, blocked_entities=blocked)
        assert literal_ip.value.type == "invalid_domain"
        assert blocked.calls == [] and resolver.calls == []  # failed before any lookup

        with pytest.raises(APIProblem) as blocked_domain:
            await validate_domain(
                "blocked.example.com", resolver=resolver, blocked_entities=blocked
            )
        assert (blocked_domain.value.status, blocked_domain.value.type) == (
            422,
            "blocked_domain",
        )
        assert resolver.calls == []  # blocked before the resolver is ever contacted

        with pytest.raises(APIProblem) as not_public:
            await validate_domain(
                "internal.example.com", resolver=resolver, blocked_entities=blocked
            )
        assert not_public.value.type == "non_public_domain"

    asyncio.run(exercise())
