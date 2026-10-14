"""The operator plane's principals (AD1, AD3, D-66): 6th staff, never a tenant role.

* ``staff``: a bearer token from the 6th STAFF identity provider (not a tenant IdP), verified for
  signature, issuer, audience and expiry; role ``operator`` or ``support``. Production reads the
  issuer, audience and key set from the environment (``config/support.yaml``); unset is 503.
* ``require_operator``: every operator route that touches tenant data. The tenant comes ONLY from
  an active support grant (``X-Support-Grant``) a tenant admin gave to THIS operator, resolved on
  every request (revoked or expired grants stop working at once). A ``read`` grant allows safe
  methods only; anything else needs a separate ``write`` grant. Every call, allowed or refused, is
  a support event and a tenant audit row the tenant can see (fail closed when it cannot be
  written).
* ``require_staff``: platform routes (tenant overview, lifecycle, quotas, flags, key references):
  the route's operator roles only; never tenant content.

GUARANTEE(test_operator_routes_fail_closed_without_the_operator_plane)
GUARANTEE(test_support_view_as_is_read_only_audited_and_visible)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request

from account_intelligence.api.problem import DependencyUnavailable
from account_intelligence.platform.ports import NotConfigured


@dataclass(frozen=True)
class StaffIdentity:
    """A verified 6th staff member."""

    staff_id: str
    role: Literal["operator", "support"]
    step_up_at: datetime | None = None


@dataclass(frozen=True)
class OperatorIdentity:
    """A 6th operator inside an audited support grant for one tenant."""

    operator_id: str
    tenant_id: UUID
    support_session_id: str
    step_up_at: datetime | None = None
    scope: str = "read"


class JwtStaffVerifier:
    """Staff tokens: RS256 from the staff IdP's key set ([VERIFY] the staff IdP's claim names)."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str,
        *,
        workspace_domain: str | None = None,
        operator_emails: frozenset[str] = frozenset(),
        support_emails: frozenset[str] = frozenset(),
    ) -> None:
        from jwt import PyJWKClient

        self.issuer, self.audience = issuer, audience
        self.workspace_domain = workspace_domain.lower().strip() if workspace_domain else None
        self.operator_emails = frozenset(email.lower().strip() for email in operator_emails)
        self.support_emails = frozenset(email.lower().strip() for email in support_emails)
        self.jwks = PyJWKClient(jwks_url, cache_keys=True)

    def verify(self, token: str) -> StaffIdentity:
        """The staff member, or PermissionError."""
        import jwt

        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            issuer: str | list[str] = self.issuer
            if self.issuer == "https://accounts.google.com":
                issuer = ["https://accounts.google.com", "accounts.google.com"]
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError:
            raise PermissionError("invalid staff token") from None
        role = cast(Literal["operator", "support"], claims.get("staff_role"))
        # Google Workspace ID tokens do not carry a product-specific staff_role by default.
        # Map only verified, workspace-domain identities from explicit server-side allowlists.
        hosted_domain = claims.get("hd")
        hosted_domain = hosted_domain.lower() if isinstance(hosted_domain, str) else ""
        if role not in ("operator", "support") and self.workspace_domain:
            email = claims.get("email")
            if (
                claims.get("email_verified") is True
                and hosted_domain == self.workspace_domain
                and isinstance(email, str)
            ):
                address = email.lower()
                if address in self.operator_emails:
                    role = "operator"
                elif address in self.support_emails:
                    role = "support"
        if role not in ("operator", "support"):
            raise PermissionError("invalid staff role")
        if self.workspace_domain and hosted_domain != self.workspace_domain:
            raise PermissionError("invalid staff workspace")
        auth_time = claims.get("auth_time")
        mfa = "mfa" in (claims.get("amr") or [])
        step = (
            datetime.fromtimestamp(auth_time, tz=UTC)
            if mfa and isinstance(auth_time, int)
            else None
        )
        return StaffIdentity(str(claims["sub"]), _role_of(role), step)


class DevStaffVerifier:
    """Dev and local-stack staff verifier for operator console testing without external IdP."""

    def __init__(self, *, now: datetime | None = None) -> None:
        self.now = now or datetime.now(UTC)

    def verify(self, token: str) -> StaffIdentity:
        """Verify a staff token and return the resolved StaffIdentity."""
        clean = token.strip()
        if ":" in clean:
            role, _, who = clean.partition(":")
        else:
            role, who = "operator", clean
        no_mfa = who.endswith(":no-mfa")
        if no_mfa:
            who = who.removesuffix(":no-mfa")
        if role not in ("operator", "support"):
            role = "operator"
        if not who:
            who = "dev-operator"
        mfa_at = None if no_mfa else self.now
        return StaffIdentity(who, _role_of(role), mfa_at)


def _role_of(raw: object) -> Literal["operator", "support"]:
    """Narrow an unverified role value to the StaffIdentity role literal."""
    return cast(Literal["operator", "support"], raw)


def staff_verifier_from_env() -> Any:
    """The production verifier (NotConfigured until the staff IdP is configured)."""
    issuer, audience, jwks = (
        os.environ.get(k) for k in ("STAFF_JWT_ISSUER", "STAFF_JWT_AUDIENCE", "STAFF_JWKS_URL")
    )
    if not (issuer and audience and jwks):
        if os.environ.get("APP_ENV") in ("dev", "local", "test"):
            return DevStaffVerifier()
        raise NotConfigured("the staff identity provider is not configured")

    def email_set(key: str) -> frozenset[str]:
        return frozenset(
            value.strip().lower() for value in os.environ.get(key, "").split(",") if value.strip()
        )

    return JwtStaffVerifier(
        issuer,
        audience,
        jwks,
        workspace_domain=os.environ.get("STAFF_WORKSPACE_DOMAIN"),
        operator_emails=email_set("STAFF_OPERATOR_EMAILS"),
        support_emails=email_set("STAFF_SUPPORT_EMAILS"),
    )


async def staff(
    request: Request, authorization: str | None = Header(default=None)
) -> StaffIdentity:
    """The verified staff member (401 without a valid staff token; 503 when unconfigured)."""
    from account_intelligence.api.tenant_plane import plane_of

    verifier: Any = getattr(plane_of(request), "staff_verifier", None)
    try:
        verifier = verifier or staff_verifier_from_env()
    except NotConfigured as e:
        raise DependencyUnavailable() from e
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="invalid token")
    try:
        found: StaffIdentity = verifier.verify(token)
    except PermissionError:
        raise HTTPException(status_code=401, detail="invalid token") from None
    return found


async def require_staff(request: Request, who: StaffIdentity = Depends(staff)) -> StaffIdentity:
    """A staff member whose role the route's catalog entry allows (403 otherwise)."""
    from account_intelligence.api.tenant_plane import plane_of
    from identity.sessions import load_policy, step_up_fresh

    policy = getattr(request.state, "route_policy", None)
    if policy is None or who.role not in policy.operator_roles:
        raise HTTPException(status_code=403, detail="forbidden")
    if not step_up_fresh(who.step_up_at, plane_of(request).clock.now(), load_policy()):
        raise HTTPException(status_code=403, detail="step-up required")
    request.state.principal = who
    # staff read counts and categories across a client's users (system scope, migration 0063);
    # the console routes return names and counts only, never content
    from account_intelligence.db.session import set_system

    set_system()
    return who


async def require_operator(
    request: Request,
    who: StaffIdentity = Depends(staff),
    grant_header: str | None = Header(default=None, alias="X-Support-Grant"),
) -> OperatorIdentity:
    """The operator inside an active grant (see the module docstring)."""
    from account_intelligence.api.guard import route_template
    from account_intelligence.api.tenant_plane import plane_of
    from account_intelligence.db.session import tenant_txn, untenanted_session
    from admin import support
    from audit.events import AuditEvent, record
    from identity.sessions import load_policy, step_up_fresh

    policy = getattr(request.state, "route_policy", None)
    if policy is None or who.role not in policy.operator_roles:
        raise HTTPException(status_code=403, detail="forbidden")
    if not step_up_fresh(who.step_up_at, plane_of(request).clock.now(), load_policy()):
        raise HTTPException(status_code=403, detail="step-up required")
    from account_intelligence.db.session import set_system

    set_system()
    try:
        grant_id = UUID(grant_header or "")
    except ValueError:
        raise HTTPException(status_code=403, detail="forbidden") from None
    now = plane_of(request).clock.now()
    try:
        async with untenanted_session() as plain:
            grant = await support.resolve(plain, grant_id, who.staff_id, now)
    except support.SupportRefused:
        raise HTTPException(status_code=403, detail="forbidden") from None
    write = request.method.upper() not in ("GET", "HEAD", "OPTIONS")
    allowed = not write or grant.scope == "write"
    template = route_template(request) or request.url.path
    request_id = getattr(request.state, "request_id", None)
    try:
        async with tenant_txn(grant.tenant_id) as s:
            await support.record(
                s,
                grant,
                method=request.method.upper(),
                route=template,
                outcome="ok" if allowed else "denied",
                request_id=request_id,
                now=now,
            )
            await record(
                s,
                AuditEvent(
                    at=now,
                    actor=who.staff_id,
                    actor_kind="support",
                    audit_class="read" if not write else "admin",
                    action="support.write" if write else "support.view",
                    resource_type="support_session",
                    resource_id=str(grant.id),
                    route=template,
                    request_id=request_id,
                    outcome="ok" if allowed else "denied",
                    support_grant_id=str(grant.id),
                ),
            )
    except Exception as error:
        # any failure to check the support grant is a 503, never an allow
        raise DependencyUnavailable() from error
    if not allowed:
        raise HTTPException(status_code=403, detail="forbidden")
    request.state.support_grant_id = str(grant.id)
    return OperatorIdentity(
        who.staff_id, grant.tenant_id, str(grant.id), who.step_up_at, grant.scope
    )
