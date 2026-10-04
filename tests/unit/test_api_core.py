from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from account_intelligence.api.auth import Identity, identity, require_role
from account_intelligence.api.domains import validate_domain
from account_intelligence.api.errors import APIProblem, NotConfigured
from account_intelligence.api.etag import if_none_match_matches
from account_intelligence.api.idempotency import (
    canonical_request_hash,
    replay_response,
)
from account_intelligence.api.main import app
from account_intelligence.api.membership import MembershipCache
from account_intelligence.api.policy import ROUTE_POLICIES
from account_intelligence.api.pricing import credits_for_depth
from account_intelligence.api.problem import install_problem_handlers
from account_intelligence.api.ratelimit import TokenBucketLimiter, enforce_rate_limit
from account_intelligence.api.routes import v1_router
from account_intelligence.config import get_settings
from tests.fakes.api import FakeBlockedEntityPort, FakeDomainResolver, FakeMembershipPort


def test_idempotency_hash_is_canonical_and_includes_all_fields() -> None:
    assert canonical_request_hash({"domain": "example.com", "depth": "standard"}) == (
        canonical_request_hash({"depth": "standard", "domain": "example.com"})
    )


def test_unconfigured_credit_price_fails_closed() -> None:
    try:
        credits_for_depth("standard", standard_credits=None, deep_credits=None)
    except APIProblem as error:
        assert error.status == 503
        assert error.type == "pricing_not_configured"
    else:
        raise AssertionError("an unapproved depth price was assumed")

    assert credits_for_depth("deep", standard_credits=7, deep_credits=19) == 19
    assert canonical_request_hash({"domain": "example.com", "depth": "deep"}) != (
        canonical_request_hash({"domain": "example.com", "depth": "standard"})
    )


def test_etag_matching_supports_weak_tags_and_wildcards() -> None:
    assert if_none_match_matches('W/"abc", "def"', '"abc"')
    assert if_none_match_matches("*", '"abc"')
    assert not if_none_match_matches('"different"', '"abc"')
    assert not if_none_match_matches(None, '"abc"')


def test_idempotency_replay_returns_original_response() -> None:
    response = {"run_id": "run-1", "status": "QUEUED"}
    assert (
        replay_response(
            stored_hash=canonical_request_hash({"domain": "example.com", "depth": "standard"}),
            incoming_hash=canonical_request_hash({"depth": "standard", "domain": "example.com"}),
            stored_response=response,
        )
        == response
    )


def test_idempotency_rejects_key_reuse_with_different_request() -> None:
    first_hash = canonical_request_hash({"domain": "example.com", "depth": "standard"})
    second_hash = canonical_request_hash({"domain": "other.example", "depth": "standard"})

    try:
        replay_response(
            stored_hash=first_hash,
            incoming_hash=second_hash,
            stored_response={"run_id": "run-1", "status": "QUEUED"},
        )
    except APIProblem as error:
        assert error.status == 409
        assert error.type == "idempotency_key_reuse"
    else:
        raise AssertionError("idempotency key reuse must fail")


def test_every_versioned_route_has_an_explicit_policy() -> None:
    openapi = app.openapi()
    routes = {
        (method.upper(), path)
        for path, operations in openapi["paths"].items()
        if path.startswith("/v1/")
        for method in operations
        if method.lower() not in {"parameters", "head", "options"}
    }
    assert routes == set(ROUTE_POLICIES)
    request_schema = openapi["components"]["schemas"]["RunRequest"]
    assert set(request_schema["properties"]) == {"domain", "depth"}
    assert "tenant_id" not in str(openapi["paths"]["/v1/runs"]["post"]["requestBody"])


def test_token_bucket_is_scoped_and_reports_retry_time() -> None:
    now = [100.0]
    limiter = TokenBucketLimiter(
        capacity=2,
        refill_per_second=1,
        max_buckets=10,
        clock=lambda: now[0],
    )

    assert limiter.consume("tenant-a:user-1", 1) == (True, 0)
    assert limiter.consume("tenant-a:user-1", 1) == (True, 0)
    assert limiter.consume("tenant-a:user-1", 1) == (False, 1)
    assert limiter.consume("tenant-b:user-1", 1) == (True, 0)

    now[0] += 1
    assert limiter.consume("tenant-a:user-1", 1) == (True, 0)


def test_membership_cache_rechecks_expired_membership() -> None:
    async def exercise() -> None:
        now = [0.0]
        tenant_id = uuid4()
        memberships = FakeMembershipPort()
        memberships.roles[(tenant_id, "user-1")] = "seller"
        cache = MembershipCache(memberships, ttl_seconds=60, monotonic=lambda: now[0])

        assert await cache.is_active(tenant_id, "user-1", "seller")
        memberships.roles.pop((tenant_id, "user-1"))
        assert await cache.is_active(tenant_id, "user-1", "seller")
        now[0] = 60.1
        assert not await cache.is_active(tenant_id, "user-1", "seller")

    asyncio.run(exercise())


def test_tenant_identity_requires_signed_claim_and_current_membership(
    monkeypatch,
) -> None:
    from account_intelligence.api import auth

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    class FakeJwks:
        def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
            del token
            return SimpleNamespace(key=public_key)

    monkeypatch.setattr(auth, "_jwks", lambda: FakeJwks())
    tenant_id = uuid4()
    membership = FakeMembershipPort()
    membership.roles[(tenant_id, "member-1")] = "seller"

    application = FastAPI()
    application.state.membership_cache = MembershipCache(membership)
    install_problem_handlers(application)
    whoami_dependency = Depends(identity)

    @application.get("/whoami")
    async def whoami(ident: Identity = whoami_dependency) -> dict[str, str]:
        return {"sub": ident.user_id, "tenant_id": str(ident.tenant_id), "role": ident.role}

    settings = get_settings()
    now = datetime.now(UTC)

    def signed_token(token_tenant: str) -> str:
        return jwt.encode(
            {
                "sub": "member-1",
                "tenant_id": token_tenant,
                "role": "seller",
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            private_key,
            algorithm="RS256",
        )

    client = TestClient(application)
    valid = client.get(
        "/whoami",
        headers={"Authorization": f"Bearer {signed_token(str(tenant_id))}"},
    )
    assert valid.status_code == 200
    assert valid.json()["tenant_id"] == str(tenant_id)

    missing = client.get("/whoami")
    assert missing.status_code == 401

    removed = client.get(
        "/whoami",
        headers={"Authorization": f"Bearer {signed_token(str(uuid4()))}"},
    )
    assert removed.status_code == 401
    assert removed.json()["type"] == "invalid_credential"


def test_domain_validation_fails_before_any_run_write() -> None:
    async def exercise() -> None:
        resolver = FakeDomainResolver()
        blocked = FakeBlockedEntityPort()
        assert (
            await validate_domain(
                "example.com",
                resolver=resolver,
                blocked_entities=blocked,
            )
            == "example.com"
        )
        assert resolver.calls == 1

        for domain in (
            "127.0.0.1",
            "localhost",
            "internal.localhost",
            "example.com.",
            "user@example.com",
            "example.com:443",
            "example.-invalid",
            "example.invalid-",
            "münich.example",
            "xn--mnich-kva.example",
        ):
            try:
                await validate_domain(
                    domain,
                    resolver=resolver,
                    blocked_entities=blocked,
                )
            except APIProblem as error:
                assert error.status == 422
            else:
                raise AssertionError(f"invalid domain was accepted: {domain}")

        private_resolver = FakeDomainResolver(("127.0.0.1", "8.8.8.8"))
        try:
            await validate_domain(
                "example.com",
                resolver=private_resolver,
                blocked_entities=blocked,
            )
        except APIProblem as error:
            assert error.type == "non_public_domain"
        else:
            raise AssertionError("private DNS answer was accepted")

        blocked_domain = "blocked.example"
        blocked_resolver = FakeDomainResolver()
        blocked = FakeBlockedEntityPort({blocked_domain})
        try:
            await validate_domain(
                blocked_domain,
                resolver=blocked_resolver,
                blocked_entities=blocked,
            )
        except APIProblem as error:
            assert error.type == "blocked_domain"
        else:
            raise AssertionError("blocked domain was accepted")
        assert blocked_resolver.calls == 0

    asyncio.run(exercise())


def test_api_problem_shapes() -> None:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/failure")
    async def failure() -> None:
        raise APIProblem(409, "conflict", "Conflict", "safe detail")

    response = TestClient(app).get("/failure")
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert response.json() == {
        "type": "conflict",
        "title": "Conflict",
        "status": 409,
        "detail": "safe detail",
        "request_id": response.headers["x-request-id"],
    }


def test_rate_limit_problem_includes_retry_after() -> None:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/limited")
    async def limited() -> None:
        enforce_rate_limit(False, 3)

    response = TestClient(app).get("/limited")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "3"
    assert response.json()["type"] == "rate_limited"


def test_missing_service_is_not_success() -> None:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/missing")
    async def missing() -> None:
        raise NotConfigured("domain resolver")

    response = TestClient(app).get("/missing")
    assert response.status_code == 503
    assert response.json()["type"] == "dependency_not_configured"
    assert "domain resolver" not in response.text


def test_route_policy_denies_viewer_from_run_creation() -> None:
    application = FastAPI()
    application.include_router(v1_router)
    install_problem_handlers(application)
    application.dependency_overrides[identity] = lambda: Identity("user-1", uuid4(), "viewer")

    response = TestClient(application).post(
        "/v1/runs",
        headers={"Idempotency-Key": "request-123"},
        json={"domain": "example.com", "depth": "standard"},
    )
    assert response.status_code == 403
    assert response.json()["type"] == "forbidden"


def test_route_without_policy_is_denied() -> None:
    application = FastAPI()
    install_problem_handlers(application)
    application.dependency_overrides[identity] = lambda: Identity("user-1", uuid4(), "admin")
    admin_dependency = Depends(require_role("admin"))

    @application.get("/v1/unmapped")
    async def unmapped(
        ident: Identity = admin_dependency,
    ) -> str:
        return ident.user_id

    response = TestClient(application).get("/v1/unmapped")
    assert response.status_code == 503
    assert response.json()["type"] == "route_policy_unavailable"


def test_run_request_rejects_tenant_field() -> None:
    application = FastAPI()
    application.include_router(v1_router)
    install_problem_handlers(application)
    application.dependency_overrides[identity] = lambda: Identity("user-1", uuid4(), "seller")

    response = TestClient(application).post(
        "/v1/runs",
        headers={"Idempotency-Key": "request-123"},
        json={"domain": "example.com", "tenant_id": str(uuid4())},
    )
    assert response.status_code == 422
    assert response.json()["type"] == "invalid_request"
    assert "tenant_id" not in response.text
