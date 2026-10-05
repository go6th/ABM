"""Verify JWT and derive tenant identity from the token."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKClient

from account_intelligence.config import get_settings


@lru_cache
def _jwks() -> PyJWKClient:
    return PyJWKClient(get_settings().jwks_url, cache_keys=True)


@dataclass(frozen=True)
class Identity:
    """The caller as the verified token states it: user, tenant and role (W3: the only tenant
    source)."""

    user_id: str
    tenant_id: UUID
    role: str


async def identity(authorization: str = Header(...)) -> Identity:
    """Verify the bearer JWT against the JWKS and return the caller; 401 on any failure."""
    settings = get_settings()
    token = authorization.removeprefix("Bearer ").strip()
    try:
        key = _jwks().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "tenant_id", "role"]},
        )
        if claims["role"] not in {"admin", "seller", "viewer"}:
            raise ValueError("invalid role")
        return Identity(claims["sub"], UUID(claims["tenant_id"]), claims["role"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="invalid token") from None


def require_role(*roles: str) -> Callable[..., Awaitable[Identity]]:
    """A dependency that admits only the listed roles (admin, seller, viewer); 403 otherwise."""

    async def dep(ident: Identity = Depends(identity)) -> Identity:
        """Resolve the caller and refuse roles outside the allowed set."""
        # Membership is checked in the tenant transaction on every request. This fails closed
        # during a database outage and makes revocation effective immediately; a bounded cache
        # can be introduced once a shared cache adapter exists.
        from sqlalchemy import text

        from account_intelligence.db.session import tenant_txn

        try:
            async with tenant_txn(ident.tenant_id) as session:
                stored_role = await session.execute(
                    text("SELECT role FROM memberships WHERE user_id = :user_id"),
                    {"user_id": ident.user_id},
                )
                member_role = stored_role.scalar_one_or_none()
        except Exception as error:
            from account_intelligence.api.problem import DependencyUnavailable

            raise DependencyUnavailable() from error
        if member_role is None or member_role != ident.role:
            raise HTTPException(status_code=401, detail="invalid token")
        if ident.role not in roles:
            raise HTTPException(status_code=403, detail="forbidden")
        return ident

    return dep
