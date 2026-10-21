"""src/api/auth.py - verify JWT, derive identity. Tenant comes from the token, never from the body."""

from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKClient

_jwks = PyJWKClient(os.environ["JWKS_URL"], cache_keys=True)


@dataclass(frozen=True)
class Identity:
    user_id: str
    tenant_id: UUID
    role: str


async def identity(authorization: str = Header(...)) -> Identity:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        key = _jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=os.environ["JWT_AUDIENCE"],
            issuer=os.environ["JWT_ISSUER"],
            options={"require": ["exp", "iat", "sub"]},
        )
        return Identity(claims["sub"], UUID(claims["tenant_id"]), claims["role"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="invalid token")


def require_role(*roles: str):
    async def dep(ident: Identity = Depends(identity)) -> Identity:
        if ident.role not in roles:  # also re-check membership in DB for revocation (cached 60 s)
            raise HTTPException(status_code=403, detail="forbidden")
        return ident

    return dep
