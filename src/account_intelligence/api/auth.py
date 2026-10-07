"""Verify the caller and derive tenant identity from the verified credential only (W3, ID1, ID2).

Two credentials are accepted on tenant routes:

* the platform session cookie (``identity.sessions``): the cookie's token hash finds the tenant
  through the definer lookup ``session_tenant``; the session must be live in that tenant and the
  role is the member's STORED role;
* a bearer JWT from the configured issuer (``sub``, ``tenant_id``, ``role``), verified for
  signature, issuer, audience and expiry; its role must equal the stored role.

Either way membership is re-checked through ``MembershipCache`` (about 60 s, ARCH): a removed or
suspended member, or a member of a suspended tenant, loses access within that bound.

GUARANTEE(test_membership_removal_and_role_change_take_effect_within_the_bound)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Literal
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, Request
from jwt import PyJWKClient

from account_intelligence.config import get_settings
from identity.roles import is_role


@lru_cache
def _jwks() -> PyJWKClient:
    return PyJWKClient(get_settings().jwks_url, cache_keys=True)


@dataclass(frozen=True)
class Identity:
    """The caller as the verified credential states it: user, tenant and role (W3: the only
    tenant source), when the person last stepped up (an MFA), which credential it was, and the
    session id for cookie sessions (CSRF binding)."""

    user_id: str
    tenant_id: UUID
    role: str
    step_up_at: datetime | None = None
    kind: Literal["jwt", "session"] = "jwt"
    session_id: str | None = None
    amr: tuple[str, ...] = field(default=())


def _cookie_name() -> str:
    from identity.sessions import load_policy

    return load_policy().session_cookie


async def _from_session(request: Request, token: str) -> Identity:
    from sqlalchemy import text

    from account_intelligence.api.tenant_plane import plane_of
    from account_intelligence.db.session import tenant_txn, untenanted_session
    from identity import membership, sessions

    plane = plane_of(request)
    now = plane.clock.now()
    async with untenanted_session() as s:
        tenant = (
            await s.execute(text("SELECT session_tenant(:h)"), {"h": sessions.token_hash(token)})
        ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=401, detail="invalid session")
    tenant_id = UUID(str(tenant))
    async with tenant_txn(tenant_id) as s:
        live = await sessions.load(s, token, now, plane.policy)
        role = await membership.active_role(s, live.user_id) if live else None
    if live is None or role is None:
        raise HTTPException(status_code=401, detail="invalid session")
    return Identity(
        live.user_id, tenant_id, role, live.step_up_at, "session", str(live.id), live.amr
    )


def _from_bearer(token: str) -> Identity:
    settings = get_settings()
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
        if not is_role(claims["role"]):
            raise ValueError("invalid role")
        return Identity(claims["sub"], UUID(claims["tenant_id"]), claims["role"], step_up(claims))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="invalid token") from None


async def identity(request: Request, authorization: str | None = Header(default=None)) -> Identity:
    """The verified caller from the bearer JWT or the session cookie; 401 on any failure."""
    if authorization:
        return _from_bearer(authorization.removeprefix("Bearer ").strip())
    token = request.cookies.get(_cookie_name())
    if token:
        return await _from_session(request, token)
    raise HTTPException(status_code=401, detail="authentication required")


def step_up(claims: dict[str, object]) -> datetime | None:
    """When the token says the person completed an MFA: ``amr`` lists ``mfa`` and ``auth_time``
    is an integer epoch. Anything else is no step-up (never assumed). [VERIFY] the identity
    provider's claim names (OIDC ``amr``/``auth_time``) before production."""
    amr = claims.get("amr")
    auth_time = claims.get("auth_time")
    if not isinstance(amr, list) or "mfa" not in amr:
        return None
    if isinstance(auth_time, bool) or not isinstance(auth_time, int):
        return None
    return datetime.fromtimestamp(auth_time, tz=UTC)


def accepted_roles(roles: tuple[str, ...]) -> frozenset[str]:
    """The roles a route admits; ``analyst`` may do everything a seller may (D-61)."""
    out = set(roles)
    if "seller" in out:
        out.add("analyst")
    return frozenset(out)


async def check_membership(request: Request, ident: Identity) -> None:
    """Re-check the caller's membership through the app's cache (about 60 s); 401 when the
    member is gone, suspended, re-roled, or the tenant is suspended; 503 when the database is
    unreachable (fail closed, never cached)."""
    from account_intelligence.api.membership import cache_of
    from account_intelligence.api.problem import DependencyUnavailable

    try:
        active = await cache_of(request).is_active(ident.tenant_id, ident.user_id, ident.role)
    except Exception as error:
        raise DependencyUnavailable() from error
    if not active:
        raise HTTPException(status_code=401, detail="invalid token")


def require_role(*roles: str) -> Callable[..., Awaitable[Identity]]:
    """A dependency that admits only the listed roles (analyst wherever seller is); 403
    otherwise; membership re-checked (401)."""
    allowed = accepted_roles(roles)

    async def dep(request: Request, ident: Identity = Depends(identity)) -> Identity:
        """Resolve the caller, re-check membership and refuse roles outside the allowed set."""
        await check_membership(request, ident)
        if ident.role not in allowed:
            raise HTTPException(status_code=403, detail="forbidden")
        return ident

    return dep


def principal(request: Request) -> Any:
    """The principal the router guard authorised for this request."""
    return getattr(request.state, "principal", None)
