"""Verify JWT identity, then confirm its tenant membership before authorization.

Guarantees: tenant identity is taken only from a verified claim and membership
is rechecked within the cache bound (GUARANTEE(test_membership_cache_rechecks_expired_membership)).
Missing membership service state and database failures deny access.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, cast
from uuid import UUID

import jwt
from fastapi import Depends, Header, Request
from jwt import PyJWKClient
from sqlalchemy.exc import SQLAlchemyError

from account_intelligence.api.errors import APIProblem
from account_intelligence.api.membership import MembershipCache
from account_intelligence.api.policy import policy_for
from account_intelligence.config import get_settings

Role = Literal["admin", "seller", "viewer"]


@lru_cache
def _jwks() -> PyJWKClient:
    return PyJWKClient(get_settings().jwks_url, cache_keys=True)


@dataclass(frozen=True)
class Identity:
    user_id: str
    tenant_id: UUID
    role: Role
    principal_type: Literal["jwt"] = "jwt"


async def identity(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Identity:
    """Authenticate a bearer token and bind it to an active tenant membership."""
    settings = get_settings()
    scheme, separator, token = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise APIProblem(
            401,
            "invalid_credential",
            "Unauthorized",
            "A valid bearer token is required.",
        )
    try:
        signing_key = _jwks().get_signing_key_from_jwt(token.strip()).key
        claims = jwt.decode(
            token.strip(),
            signing_key,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "tenant_id", "role"]},
        )
        user_id = claims["sub"]
        tenant_id = UUID(claims["tenant_id"])
        role = claims["role"]
        if (
            not isinstance(user_id, str)
            or not user_id
            or role
            not in {
                "admin",
                "seller",
                "viewer",
            }
        ):
            raise ValueError("invalid identity claims")
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise APIProblem(
            401,
            "invalid_credential",
            "Unauthorized",
            "The bearer token is invalid or expired.",
        ) from None

    checker = cast(MembershipCache, request.app.state.membership_cache)
    try:
        active = await checker.is_active(tenant_id, user_id, role)
    except SQLAlchemyError:
        raise APIProblem(
            503,
            "membership_unavailable",
            "Authentication temporarily unavailable",
            "Membership could not be verified.",
        ) from None
    if not active:
        raise APIProblem(
            401,
            "invalid_credential",
            "Unauthorized",
            "The bearer token is invalid or expired.",
        )
    return Identity(user_id=user_id, tenant_id=tenant_id, role=role)


def require_role(*roles: Role) -> Callable[..., Awaitable[Identity]]:
    """Enforce the central route policy and deny roles outside its allow-list."""

    async def dependency(
        request: Request,
        ident: Identity = Depends(identity),
    ) -> Identity:
        route = request.scope.get("route")
        route_path = getattr(route, "path_format", None)
        if not isinstance(route_path, str):
            route_path = getattr(route, "path", None)
        if isinstance(route_path, str) and not route_path.startswith("/v1/"):
            route_path = f"/v1{route_path}"
        policy = policy_for(request.method, route_path) if isinstance(route_path, str) else None
        if policy is None or policy.roles != frozenset(roles):
            raise APIProblem(
                503,
                "route_policy_unavailable",
                "Service temporarily unavailable",
                "The route authorization policy is not configured.",
            )
        if ident.principal_type not in policy.principal_types or ident.role not in policy.roles:
            raise APIProblem(403, "forbidden", "Forbidden", "This action is not permitted.")
        return ident

    return dependency
