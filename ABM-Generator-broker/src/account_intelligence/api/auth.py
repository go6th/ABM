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
    user_id: str
    tenant_id: UUID
    role: str


async def identity(authorization: str = Header(...)) -> Identity:
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
            options={"require": ["exp", "iat", "sub"]},
        )
        return Identity(claims["sub"], UUID(claims["tenant_id"]), claims["role"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="invalid token") from None


def require_role(*roles: str) -> Callable[..., Awaitable[Identity]]:
    async def dep(ident: Identity = Depends(identity)) -> Identity:
        if ident.role not in roles:  # also re-check membership in DB for revocation (cached 60 s)
            raise HTTPException(status_code=403, detail="forbidden")
        return ident

    return dep
