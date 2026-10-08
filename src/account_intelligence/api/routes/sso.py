"""SSO and session routes (``/v1/sso``; ID1 to ID3, D-62).

Anonymous (``public_router``; the catalog guard still applies, nothing else does):

* ``GET /v1/sso/{slug}/start``: open a sign-in for the connection and redirect to its IdP; sets
  the pre-auth cookie that binds the sign-in to this browser (``SameSite=None`` for SAML, whose
  ACS is a cross-site POST; ``Lax`` for OIDC, whose callback is a top-level GET).
* ``GET /v1/sso/oidc/callback`` and ``POST /v1/sso/saml/acs``: finish it; set the session cookie
  (HttpOnly, Secure, SameSite=Lax, ``__Host-``) and redirect to a LOCAL path only.

Authenticated (``router``; guard plus ``authorize``): ``GET /v1/sso/session`` (who am I, the
CSRF token), ``POST /v1/sso/logout``, ``POST /v1/sso/logout-all`` and the admin's IdP connection
management. Every refusal answers one generic message; the reason is in ``auth_events``.
"""

from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import ANONYMOUS, register, tenant_route
from account_intelligence.api.tenant_plane import CSRF_KEY_REF, plane_of
from account_intelligence.db.session import tenant_txn
from identity import connections, sessions
from identity.broker import SignedIn, SignInRefused
from identity.csrf import csrf_token
from identity.roles import ROLES

public_router = APIRouter(prefix="/sso", tags=["sso"])
router = APIRouter(prefix="/sso", tags=["sso"])
register("GET", "/v1/sso/{slug}/start", ANONYMOUS)
register("GET", "/v1/sso/oidc/callback", ANONYMOUS)
register("POST", "/v1/sso/saml/acs", ANONYMOUS)
register("GET", "/v1/sso/session", tenant_route("read_public", "none"))
register("POST", "/v1/sso/logout", tenant_route("revoke_own_session", "none"))
register("POST", "/v1/sso/logout-all", tenant_route("revoke_own_session", "none"))
register("GET", "/v1/sso/connections", tenant_route("manage_sso", "read", step_up=False))
register("POST", "/v1/sso/connections", tenant_route("manage_sso", "admin"))
register("PUT", "/v1/sso/connections/{connection_id}", tenant_route("manage_sso", "admin"))

_REFUSED = "sign-in could not be completed"


def _session_cookie(response: Response, signed: SignedIn, request: Request) -> None:
    policy = plane_of(request).policy
    response.set_cookie(
        policy.session_cookie,
        signed.token,
        max_age=policy.absolute_timeout_s,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(policy.preauth_cookie, path="/", secure=True, httponly=True)


@public_router.get("/{slug}/start")
async def start(
    slug: str,
    request: Request,
    return_to: str = Query(default="/", max_length=200),
    step_up: bool = Query(default=False),
) -> Response:
    """Open a sign-in and redirect to the tenant's IdP."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,62}", slug):
        raise HTTPException(404, "not found")
    plane = plane_of(request)
    try:
        started = await plane.broker().start(
            slug, plane.clock.now(), return_to=return_to, force_login=step_up
        )
    except SignInRefused:
        raise HTTPException(404, "not found") from None
    response = RedirectResponse(started.redirect_url, status_code=302)
    saml = "SAMLRequest=" in started.redirect_url
    response.set_cookie(
        plane.policy.preauth_cookie,
        started.preauth_cookie,
        max_age=plane.policy.state_ttl_s,
        path="/",
        secure=True,
        httponly=True,
        samesite="none" if saml else "lax",
    )
    return response


def _finish(request: Request, signed: SignedIn) -> Response:
    response = RedirectResponse(signed.return_to, status_code=303)
    _session_cookie(response, signed, request)
    return response


@public_router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    state: str = Query(min_length=16, max_length=200),
    code: str = Query(min_length=1, max_length=2000),
) -> Response:
    """Finish an OIDC sign-in."""
    plane = plane_of(request)
    policy = plane.policy
    try:
        signed = await plane.broker().complete_oidc(
            state=state,
            code=code,
            preauth=request.cookies.get(policy.preauth_cookie),
            now=plane.clock.now(),
            old_session_token=request.cookies.get(policy.session_cookie),
        )
    except SignInRefused:
        raise HTTPException(401, _REFUSED) from None
    return _finish(request, signed)


@public_router.post("/saml/acs")
async def saml_acs(
    request: Request,
    saml_response: str = Form(alias="SAMLResponse", max_length=400_000),
    relay_state: str = Form(alias="RelayState", min_length=16, max_length=200),
) -> Response:
    """Finish a SAML sign-in (HTTP-POST binding)."""
    plane = plane_of(request)
    policy = plane.policy
    try:
        signed = await plane.broker().complete_saml(
            relay_state=relay_state,
            saml_response=saml_response,
            preauth=request.cookies.get(policy.preauth_cookie),
            now=plane.clock.now(),
            old_session_token=request.cookies.get(policy.session_cookie),
        )
    except SignInRefused:
        raise HTTPException(401, _REFUSED) from None
    return _finish(request, signed)


@router.get("/session", response_model=C.Session)
async def whoami(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The signed-in user, their role, the CSRF token for this session and whether step-up is
    fresh. No token or secret is returned."""
    plane = plane_of(request)
    body: dict[str, Any] = {
        "user_id": ident.user_id,
        "role": ident.role,
        "credential": ident.kind,
        "step_up_fresh": sessions.step_up_fresh(ident.step_up_at, plane.clock.now(), plane.policy),
    }
    if ident.kind == "session" and ident.session_id:
        body["csrf_token"] = csrf_token(await plane.key(CSRF_KEY_REF), ident.session_id)
    return body


@router.post("/logout", responses={200: {"model": C.SignedOut}})
async def logout(request: Request, ident: Identity = Depends(authorize)) -> Response:
    """End this session (cookie sessions only)."""
    plane = plane_of(request)
    if ident.kind != "session" or ident.session_id is None:
        raise HTTPException(409, "not a cookie session")
    async with tenant_txn(ident.tenant_id) as s:
        await sessions.revoke(s, UUID(ident.session_id), "logout", plane.clock.now())
    response = JSONResponse({"signed_out": True})
    response.delete_cookie(plane.policy.session_cookie, path="/", secure=True, httponly=True)
    return response


@router.post("/logout-all", responses={200: {"model": C.SessionsEnded}})
async def logout_all(request: Request, ident: Identity = Depends(authorize)) -> Response:
    """End every session of this user ("sign out everywhere")."""
    plane = plane_of(request)
    async with tenant_txn(ident.tenant_id) as s:
        n = await sessions.revoke_all(s, ident.user_id, "sign_out_everywhere", plane.clock.now())
    response = JSONResponse({"sessions_ended": n})
    response.delete_cookie(plane.policy.session_cookie, path="/", secure=True, httponly=True)
    return response


class ConnectionBody(BaseModel):
    """An IdP connection as an admin configures it. No tenant field; the secret is write-only."""

    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    protocol: Literal["oidc", "saml"]
    issuer: str = Field(min_length=1, max_length=500)
    client_id: str | None = Field(default=None, max_length=300)
    client_secret: str | None = Field(default=None, min_length=8, max_length=500)
    authorization_endpoint: str | None = Field(default=None, pattern=r"^https://", max_length=500)
    token_endpoint: str | None = Field(default=None, pattern=r"^https://", max_length=500)
    jwks_uri: str | None = Field(default=None, pattern=r"^https://", max_length=500)
    saml_sso_url: str | None = Field(default=None, pattern=r"^https://", max_length=500)
    saml_idp_cert_pem: str | None = Field(default=None, max_length=20000)
    sp_entity_id: str | None = Field(default=None, max_length=500)
    verified_domains: list[str] = Field(default_factory=list, max_length=50)
    group_role_map: dict[str, str] = Field(default_factory=dict, max_length=200)
    jit_enabled: bool = False
    require_admin_mfa: bool = True

    @field_validator("group_role_map")
    @classmethod
    def _roles(cls, v: dict[str, str]) -> dict[str, str]:
        if any(role not in ROLES or not group or len(group) > 200 for group, role in v.items()):
            raise ValueError("every mapped group needs one of the four tenant roles")
        return v

    @field_validator("verified_domains")
    @classmethod
    def _domains(cls, v: list[str]) -> list[str]:
        out = sorted({d.strip().lower() for d in v})
        if any(not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+", d) for d in out):
            raise ValueError("verified domains must be domains")
        return out


@router.get("/connections", response_model=C.IdpConnections)
async def list_connections(
    request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """The tenant's IdP connections (secrets are never returned, only whether one is set)."""
    async with tenant_txn(ident.tenant_id) as s:
        return {"connections": await connections.listing(s)}


async def _secret_ref(request: Request, body: ConnectionBody, tenant: UUID) -> str | None:
    if body.client_secret is None:
        return None
    plane = plane_of(request)
    return await plane.secrets.put(f"tenants/{tenant}/idp/{body.slug}", body.client_secret.encode())


@router.post("/connections", response_model=C.IdpConnection, status_code=201)
async def create_connection(
    body: ConnectionBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Create an IdP connection (admin with a fresh step-up)."""
    ref = await _secret_ref(request, body, ident.tenant_id)
    try:
        async with tenant_txn(ident.tenant_id) as s:
            created = await connections.create(s, body.model_dump(exclude={"client_secret"}), ref)
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="sso_connection_create",
                resource_type="idp_connection",
                resource_id=created["id"],
            )
            return created
    except connections.ConnectionConflict:
        raise HTTPException(409, "connection could not be created") from None


class ConnectionUpdate(ConnectionBody):
    """An update carries the version it was read at."""

    expected_version: int = Field(ge=1)
    status: Literal["active", "disabled"] = "active"


@router.put("/connections/{connection_id}", response_model=C.IdpConnection)
async def update_connection(
    connection_id: UUID,
    body: ConnectionUpdate,
    request: Request,
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """Update a connection (conditional on its version); a new secret replaces the reference."""
    ref = await _secret_ref(request, body, ident.tenant_id)
    values = body.model_dump(exclude={"client_secret", "expected_version"})
    try:
        async with tenant_txn(ident.tenant_id) as s:
            updated = await connections.update(s, connection_id, values, ref, body.expected_version)
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="sso_connection_update",
                resource_type="idp_connection",
                resource_id=connection_id,
            )
            return updated
    except connections.ConnectionConflict:
        raise HTTPException(409, "connection changed or not found") from None
