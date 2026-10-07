"""The in-app identity broker (D-62 proposal): tenant IdP sign-in to a platform session.

1. ``start`` (anonymous): the login URL names a connection SLUG; the tenant is the connection's
   owner (definer lookup ``idp_connection_tenant``), never a request field or an IdP claim. A
   pending sign-in stores the hash of a random ``state``, the nonce, the PKCE verifier (OIDC) or
   the AuthnRequest id (SAML), and the hash of a random pre-auth cookie that binds it to this
   browser. The browser is redirected to the IdP.
2. ``complete_oidc`` / ``complete_saml``: the state hash finds the tenant (definer lookup
   ``login_state_tenant``); inside that tenant's transaction the pending sign-in must exist, be
   unexpired, unconsumed (consumed here, once) and bound to the presented pre-auth cookie. The
   ID token or SAML response is verified (``identity.oidc``, ``identity.saml``), a SAML assertion
   id is recorded once (replay refused), the sign-in decision is taken (``identity.provisioning``)
   and a NEW session is created (rotation on sign-in; a presented old session is revoked).

Every outcome writes ``auth_events`` (reason codes only). Failures raise ``SignInRefused`` with a
fixed reason; the HTTP layer answers with one generic message.

GUARANTEE(test_sso_positive_flows_create_a_session_in_the_connection_tenant)
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from identity import membership, oidc, provisioning, saml, sessions
from identity.ports import IdentityProvider, IdentityProviderError, OidcEndpoints
from tenancy.ports import SecretStore

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
PlainFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class SignInRefused(PermissionError):
    """A sign-in that must not produce a session; ``reason`` is a fixed code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Started:
    """Where to send the browser, and the pre-auth cookie to set (HttpOnly, short-lived)."""

    redirect_url: str
    preauth_cookie: str


@dataclass(frozen=True)
class SignedIn:
    """A new session: the cookie token (set once, never stored), who, where to go next."""

    token: str
    session_id: UUID
    tenant_id: UUID
    user_id: str
    role: str
    return_to: str


@dataclass
class Broker:
    """The broker's collaborators: transactions, the IdP port, secrets, URLs and policy."""

    txn: TxnFactory
    plain: PlainFactory
    idp: IdentityProvider
    secrets: SecretStore
    base_url: str
    policy: sessions.SessionPolicy

    @property
    def oidc_redirect_uri(self) -> str:
        """The EXACT redirect URI registered with every OIDC IdP."""
        return f"{self.base_url}/v1/sso/oidc/callback"

    @property
    def saml_acs_url(self) -> str:
        """The EXACT assertion consumer service URL registered with every SAML IdP."""
        return f"{self.base_url}/v1/sso/saml/acs"

    async def _resolve_slug(self, slug: str) -> tuple[UUID, UUID]:
        async with self.plain() as s:
            row = (
                await s.execute(
                    text("SELECT tenant_id, connection_id FROM idp_connection_tenant(:s)"),
                    {"s": slug},
                )
            ).first()
        if row is None:
            raise SignInRefused("unknown_connection")
        return row.tenant_id, row.connection_id

    async def start(
        self, slug: str, now: datetime, *, return_to: str = "/", force_login: bool = False
    ) -> Started:
        """Open a pending sign-in for the connection and build the IdP redirect."""
        if not _safe_return(return_to):
            return_to = "/"
        tenant, connection = await self._resolve_slug(slug)
        state, preauth = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        async with self.txn(tenant) as s:
            conn = await _connection(s, connection)
            nonce = verifier = request_id = None
            if conn.protocol == "oidc":
                nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
                url = oidc.authorization_url(
                    authorization_endpoint=conn.authorization_endpoint,
                    client_id=conn.client_id,
                    redirect_uri=self.oidc_redirect_uri,
                    state=state,
                    nonce=nonce,
                    code_verifier=verifier,
                    force_login=force_login,
                )
            else:
                request_id = "_" + secrets.token_hex(20)
                url = saml.authn_request_url(
                    self._saml_conn(conn), request_id, state, now, force_authn=force_login
                )
            await s.execute(
                text(
                    "INSERT INTO login_states (connection_id, state_hash, browser_hash, nonce,"
                    " code_verifier, saml_request_id, redirect_uri, return_to, created_at,"
                    " expires_at) VALUES (:c, :sh, :bh, :n, :v, :rid, :ru, :rt, :now, :exp)"
                ),
                {
                    "c": connection,
                    "sh": _sha(state),
                    "bh": _sha(preauth),
                    "n": nonce,
                    "v": verifier,
                    "rid": request_id,
                    "ru": self.oidc_redirect_uri if conn.protocol == "oidc" else self.saml_acs_url,
                    "rt": return_to,
                    "now": now,
                    "exp": now + timedelta(seconds=self.policy.state_ttl_s),
                },
            )
            await sessions.event(s, "login_started", now, connection_id=connection)
        return Started(url, preauth)

    def _saml_conn(self, conn: Any) -> saml.SamlConnection:
        return saml.SamlConnection(
            issuer=conn.issuer,
            idp_cert_pem=conn.saml_idp_cert_pem,
            sp_entity_id=conn.sp_entity_id,
            acs_url=self.saml_acs_url,
            sso_url=conn.saml_sso_url,
        )

    async def _claim_state(
        self, s: AsyncSession, state: str, preauth: str | None, now: datetime, protocol: str
    ) -> Any:
        pending = (
            await s.execute(
                text(
                    "SELECT id, connection_id, browser_hash, nonce, code_verifier,"
                    " saml_request_id, return_to, expires_at, consumed_at"
                    " FROM login_states WHERE state_hash = :h"
                ),
                {"h": _sha(state)},
            )
        ).first()
        if pending is None:
            raise SignInRefused("unknown_state")
        if not preauth or not secrets.compare_digest(pending.browser_hash, _sha(preauth)):
            raise SignInRefused("state_not_bound_to_browser")
        if pending.consumed_at is not None:
            raise SignInRefused("state_replayed")
        if now >= pending.expires_at:
            raise SignInRefused("state_expired")
        claimed = (
            await s.execute(
                text(
                    "UPDATE login_states SET consumed_at = :now"
                    " WHERE id = :i AND consumed_at IS NULL RETURNING id"
                ),
                {"now": now, "i": pending.id},
            )
        ).first()
        if claimed is None:
            raise SignInRefused("state_replayed")
        conn = await _connection(s, pending.connection_id)
        if conn.protocol != protocol or conn.status != "active":
            raise SignInRefused("connection_mismatch")
        return pending, conn

    async def _state_tenant(self, state: str) -> UUID:
        if not state or len(state) > 200:
            raise SignInRefused("unknown_state")
        async with self.plain() as s:
            tenant = (
                await s.execute(text("SELECT login_state_tenant(:h)"), {"h": _sha(state)})
            ).scalar_one_or_none()
        if tenant is None:
            raise SignInRefused("unknown_state")
        return UUID(str(tenant))

    async def complete_oidc(
        self,
        *,
        state: str,
        code: str,
        preauth: str | None,
        now: datetime,
        old_session_token: str | None = None,
    ) -> SignedIn:
        """Finish an OIDC sign-in (every ID1 check)."""
        tenant = await self._state_tenant(state)
        refused: SignInRefused | None = None
        async with self.txn(tenant) as s:
            try:
                pending, conn = await self._claim_state(s, state, preauth, now, "oidc")
                endpoints = OidcEndpoints(
                    conn.issuer, conn.client_id, conn.token_endpoint, conn.jwks_uri
                )
                secret = (
                    (await self.secrets.read(conn.client_secret_ref)).decode()
                    if conn.client_secret_ref
                    else None
                )
                try:
                    id_token = await self.idp.exchange_code(
                        endpoints,
                        code=code,
                        code_verifier=pending.code_verifier,
                        redirect_uri=self.oidc_redirect_uri,
                        client_secret=secret,
                    )
                    keys = await self.idp.jwks(endpoints)
                except IdentityProviderError as e:
                    raise SignInRefused(e.reason) from None
                try:
                    claims = oidc.verify_id_token(
                        id_token,
                        jwks=keys,
                        issuer=conn.issuer,
                        client_id=conn.client_id,
                        nonce=pending.nonce,
                        now=now,
                        skew_s=self.policy.clock_skew_s,
                    )
                except oidc.OidcError as e:
                    raise SignInRefused(e.reason) from None
                async with s.begin_nested():
                    return await self._admit(
                        s,
                        conn,
                        tenant,
                        now,
                        subject=claims.subject,
                        email=claims.email,
                        email_verified=claims.email_verified,
                        groups=claims.groups,
                        amr=claims.amr,
                        return_to=pending.return_to,
                        old_token=old_session_token,
                    )
            except SignInRefused as e:
                # the consumed state and the failure event commit; whatever _admit wrote did not
                refused = e
                await sessions.event(s, "login_failed", now, reason=e.reason)
        assert refused is not None
        raise refused

    async def complete_saml(
        self,
        *,
        relay_state: str,
        saml_response: str,
        preauth: str | None,
        now: datetime,
        old_session_token: str | None = None,
    ) -> SignedIn:
        """Finish a SAML sign-in (every ID1 check, replay cache included)."""
        tenant = await self._state_tenant(relay_state)
        refused: SignInRefused | None = None
        async with self.txn(tenant) as s:
            try:
                pending, conn = await self._claim_state(s, relay_state, preauth, now, "saml")
                try:
                    ident = saml.parse_response(
                        saml_response,
                        conn=self._saml_conn(conn),
                        expected_request_id=pending.saml_request_id,
                        now=now,
                        skew_s=self.policy.clock_skew_s,
                        max_bytes=self.policy.max_assertion_bytes,
                    )
                except saml.SamlError as e:
                    raise SignInRefused(e.reason) from None
                try:
                    async with s.begin_nested():
                        await s.execute(
                            text(
                                "INSERT INTO saml_assertion_ids (connection_id, assertion_id,"
                                " not_on_or_after, seen_at) VALUES (:c, :a, :n, :now)"
                            ),
                            {
                                "c": conn.id,
                                "a": ident.assertion_id,
                                "n": ident.not_on_or_after,
                                "now": now,
                            },
                        )
                except IntegrityError:
                    raise SignInRefused("assertion_replayed") from None
                context = ident.authn_context or ""
                mfa = ("mfa",) if "MultiFactor" in context else ()
                async with s.begin_nested():
                    return await self._admit(
                        s,
                        conn,
                        tenant,
                        now,
                        subject=ident.subject,
                        email=ident.email,
                        email_verified=ident.email is not None,
                        groups=ident.groups,
                        amr=mfa,
                        return_to=pending.return_to,
                        old_token=old_session_token,
                    )
            except SignInRefused as e:
                refused = e
                await sessions.event(s, "login_failed", now, reason=e.reason)
        assert refused is not None
        raise refused

    async def _admit(
        self,
        s: AsyncSession,
        conn: Any,
        tenant: UUID,
        now: datetime,
        *,
        subject: str,
        email: str | None,
        email_verified: bool,
        groups: tuple[str, ...],
        amr: tuple[str, ...],
        return_to: str,
        old_token: str | None,
    ) -> SignedIn:
        user_id = f"{conn.slug}:{subject}"
        member = await membership.get(s, user_id)
        decision = provisioning.decide(
            groups=groups,
            group_role_map=dict(conn.group_role_map or {}),
            member_role=member.role if member else None,
            member_status=member.status if member else None,
            jit_enabled=bool(conn.jit_enabled),
            email=email,
            email_verified=email_verified,
            verified_domains=list(conn.verified_domains or ()),
        )
        if not decision.admit or decision.role is None:
            raise SignInRefused(decision.reason or "refused")
        if (await membership.active_role(s, user_id)) is None and not decision.provision:
            raise SignInRefused("tenant_suspended_or_inactive")
        if decision.provision:
            from metering import limits

            try:
                await limits.consume(s, "seats", now)
            except limits.LimitReached:
                raise SignInRefused("plan_seats_reached") from None
            await membership.add(
                s,
                user_id=user_id,
                role=decision.role,
                actor=f"idp:{conn.slug}",
                now=now,
                email=email,
                provisioned=True,
            )
            await sessions.event(s, "provisioned", now, user_id=user_id, connection_id=conn.id)
            if (await membership.active_role(s, user_id)) is None:
                raise SignInRefused("tenant_suspended_or_inactive")
        admin_mfa = conn.require_admin_mfa and self.policy.admin_requires_mfa
        if decision.role == "admin" and admin_mfa and "mfa" not in amr:
            raise SignInRefused("admin_requires_mfa")
        if old_token:
            old = await sessions.load(s, old_token, now, self.policy)
            if old is not None:
                await sessions.revoke(s, old.id, "rotated", now)
        token, sid = await sessions.create(
            s,
            user_id=user_id,
            now=now,
            policy=self.policy,
            connection_id=conn.id,
            amr=amr,
            step_up="mfa" in amr,
        )
        return SignedIn(token, sid, tenant, user_id, decision.role, return_to)


async def _connection(s: AsyncSession, connection_id: UUID) -> Any:
    row = (
        await s.execute(
            text(
                "SELECT id, slug, protocol, issuer, client_id, client_secret_ref,"
                " authorization_endpoint, token_endpoint, jwks_uri, saml_sso_url,"
                " saml_idp_cert_pem, sp_entity_id, verified_domains, group_role_map,"
                " jit_enabled, require_admin_mfa, status FROM idp_connections WHERE id = :c"
            ),
            {"c": connection_id},
        )
    ).first()
    if row is None:
        raise SignInRefused("unknown_connection")
    return row


def _safe_return(path: str) -> bool:
    """Only a local absolute path (no scheme, no host, no ``//``, no backslash) may be a
    post-sign-in destination: the open-redirect defence."""
    return (
        path.startswith("/")
        and not path.startswith("//")
        and "\\" not in path
        and "://" not in path
        and len(path) <= 200
        and all(c.isalnum() or c in "/_?=&.-" for c in path)
    )
