"""Connector OAuth (CN1): consent bound to tenant, user and connection; PKCE; the exact
redirect URI; every scope the table requires and no wider data scope (sign-in scopes such as
openid or User.Read are ignored, ``scopes.scopes_ok``); tokens behind Secret Manager references,
never logged, never in an audit row or a response; refresh bounded, and a scope change or a
revoked grant disables the connection (fail closed, with a clear state). A provider that is down
is ``provider_unavailable`` at consent and a transient ``token_refresh_failed`` on refresh (the
caller decides whether that disables anything). A refresh token the provider rotates is written
back before the new access token is used. The one writer of ``connector_connections`` and
``connector_oauth_states``.

GUARANTEE(test_connector_oauth_is_bound_minimal_and_fails_closed)
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
from sqlalchemy import text

from connectors_client import access_log
from connectors_client.ports import OAuthClient, SourceError, TokenRevoked, TokenSet
from connectors_client.scopes import load, scopes_ok, spec
from identity.oidc import pkce_challenge
from tenancy.ports import SecretStore

# reasons that say the provider could not be reached, not that the grant is bad: a retry later
# may succeed, so a caller that can wait keeps the connection active
TRANSIENT_REASONS = frozenset({"token_refresh_failed", "provider_unavailable"})


class ConnectorRefused(PermissionError):
    """A consent, exchange or refresh that must not produce a usable grant; fixed reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @property
    def transient(self) -> bool:
        """True when the provider was unreachable (retry later), not when the grant is bad."""
        return self.reason in TRANSIENT_REASONS


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Started:
    """A pending consent: the connection and where to send the admin."""

    connection_id: UUID
    authorize_url: str


async def start(
    s: Any,
    *,
    kind: str,
    user: str,
    client_id: str,
    redirect_uri: str,
    now: datetime,
    personal: bool = False,
) -> Started:
    """Create a pending connection and its single-use consent state. ``personal``: a Mail and
    drive connection owned by ``user`` alone (never a tenant-wide source)."""
    sp = spec(kind)
    if not sp.enabled:
        raise ConnectorRefused("connector_not_enabled")
    connection_id: UUID = (
        await s.execute(
            text(
                "INSERT INTO connector_connections (kind, status, created_by, created_at,"
                " owner_user_id, provider) VALUES (:k, 'pending', :u, :now, :owner, :p)"
                " RETURNING id"
            ),
            {
                "k": kind,
                "u": user,
                "now": now,
                # private workspaces (0063): every connection is its creator's; a personal one
                # also names its provider
                "owner": user,
                "p": sp.provider if personal else None,
            },
        )
    ).scalar_one()
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    ttl = load()[1].oauth_state_ttl_s
    await s.execute(
        text(
            "INSERT INTO connector_oauth_states (state_hash, connection_id, user_id,"
            " code_verifier, created_at, expires_at) VALUES (:h, :c, :u, :v, :now, :exp)"
        ),
        {
            "h": _sha(state),
            "c": connection_id,
            "u": user,
            "v": verifier,
            "now": now,
            "exp": now + timedelta(seconds=ttl),
        },
    )
    await access_log.log(
        s,
        connection_id=connection_id,
        principal=user,
        kind=kind,
        purpose="consent",
        outcome="ok",
        at=now,
    )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(sorted(sp.scopes)),
            "state": state,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return Started(connection_id, f"{sp.authorize_endpoint}?{query}")


async def complete(
    s: Any,
    *,
    state: str,
    code: str,
    user: str,
    redirect_uri: str,
    oauth: OAuthClient,
    store: SecretStore,
    tenant_id: UUID,
    now: datetime,
) -> UUID:
    """Finish a consent: the state must be this tenant's, this user's, unexpired and unused; the
    granted scopes must cover the table's and be no wider (``scopes_ok``); the refresh token is
    stored by reference. A provider that is down or unreachable is ``provider_unavailable`` (the
    user may try again), a refused code ``consent_refused``, a grant without a refresh token
    ``no_refresh_token``."""
    row = (
        await s.execute(
            text(
                "UPDATE connector_oauth_states SET consumed_at = :now WHERE state_hash = :h"
                " AND consumed_at IS NULL AND expires_at > :now"
                " RETURNING connection_id, user_id, code_verifier"
            ),
            {"now": now, "h": _sha(state)},
        )
    ).first()
    if row is None:
        raise ConnectorRefused("unknown_or_used_state")
    if row.user_id != user:
        raise ConnectorRefused("state_bound_to_another_user")
    conn = (
        await s.execute(
            text("SELECT kind, status FROM connector_connections WHERE id = :c"),
            {"c": row.connection_id},
        )
    ).one()
    if conn.status != "pending":
        raise ConnectorRefused("connection_not_pending")
    try:
        tokens = await oauth.exchange(
            conn.kind, code=code, code_verifier=row.code_verifier, redirect_uri=redirect_uri
        )
    except TokenRevoked:
        raise ConnectorRefused("consent_refused") from None
    except (SourceError, httpx.HTTPError, OSError):
        raise ConnectorRefused("provider_unavailable") from None
    refused = (
        "scope_mismatch"
        if not scopes_ok(conn.kind, tokens.scopes)
        else "no_refresh_token"
        if not tokens.refresh_token
        else None
    )
    if refused is not None:
        await access_log.log(
            s,
            connection_id=row.connection_id,
            principal=user,
            kind=conn.kind,
            purpose="token_exchange",
            outcome="denied",
            at=now,
        )
        raise ConnectorRefused(refused)
    ref = await store.put(
        f"tenants/{tenant_id}/connectors/{row.connection_id}",
        json.dumps({"refresh_token": tokens.refresh_token}).encode(),
    )
    await s.execute(
        text(
            "UPDATE connector_connections SET status = 'active', scopes = :sc, token_ref = :ref,"
            " consented_by = :u, consented_at = :now, version = version + 1"
            " WHERE id = :c AND status = 'pending'"
        ),
        {"sc": sorted(tokens.scopes), "ref": ref, "u": user, "now": now, "c": row.connection_id},
    )
    await access_log.log(
        s,
        connection_id=row.connection_id,
        principal=user,
        kind=conn.kind,
        purpose="token_exchange",
        outcome="ok",
        at=now,
    )
    return UUID(str(row.connection_id))


async def mark_broken(s: Any, connection_id: UUID, reason: str) -> None:
    """Disable a connection with a clear reason (fail closed)."""
    await s.execute(
        text(
            "UPDATE connector_connections SET status = 'broken', broken_reason = :r,"
            " version = version + 1 WHERE id = :c AND status = 'active'"
        ),
        {"r": reason, "c": connection_id},
    )


async def replace_token_ref(s: Any, connection_id: UUID, token_ref: str) -> bool:
    """Point an active connection at a new secret version (a rotated refresh token). False when
    the connection is no longer active (disconnected or broken meanwhile)."""
    kept = (
        await s.execute(
            text(
                "UPDATE connector_connections SET token_ref = :r, version = version + 1"
                " WHERE id = :c AND status = 'active' RETURNING id"
            ),
            {"r": token_ref, "c": connection_id},
        )
    ).first()
    return kept is not None


async def access_token(
    oauth: OAuthClient,
    store: SecretStore,
    *,
    kind: str,
    token_ref: str,
    granted: tuple[str, ...],
    attempts: int,
    secret_name: str | None = None,
    on_rotated: Callable[[str], Awaitable[bool]] | None = None,
) -> TokenSet:
    """A fresh access token by refresh, at most ``attempts`` tries (outside any transaction).
    ConnectorRefused ``token_revoked`` or ``scope_changed`` disables the grant (the caller marks
    the connection broken); ``token_refresh_failed`` (transient) means the provider could not be
    reached. A refresh response without scopes keeps the granted ones (RFC 6749 section 5.1).

    When the provider rotates the refresh token (Microsoft does) and ``secret_name`` and
    ``on_rotated`` are given, the new one is written back before returning: a new secret
    version under ``secret_name``, then ``on_rotated(new_ref)`` records the reference (False:
    the connection is gone, so the new secret is destroyed and ``connection_gone`` raised), then
    the old version is destroyed. A store that cannot write keeps the old token (the provider
    may refuse it later)."""
    secret = json.loads(await store.read(token_ref))
    last: Exception | None = None
    # LOOP(connector token refresh): cap=refresh_attempts attempts; exit=refreshed, revoked,
    # scope changed, attempts exhausted (transient: token_refresh_failed)
    for _ in range(max(1, attempts)):
        try:
            tokens = await oauth.refresh(kind, secret["refresh_token"])
        except TokenRevoked:
            raise ConnectorRefused("token_revoked") from None
        except Exception as e:  # noqa: BLE001 - a transient source failure: bounded retry
            last = e
            continue
        if not scopes_ok(kind, tokens.scopes or granted) or not scopes_ok(kind, granted):
            raise ConnectorRefused("scope_changed")
        rotated = tokens.refresh_token
        if secret_name and on_rotated and rotated and rotated != secret["refresh_token"]:
            await _write_back(store, secret_name, token_ref, rotated, on_rotated)
        return tokens
    raise ConnectorRefused("token_refresh_failed") from last


async def _write_back(
    store: SecretStore,
    name: str,
    old_ref: str,
    refresh_token: str,
    on_rotated: Callable[[str], Awaitable[bool]],
) -> None:
    """Store a rotated refresh token (see ``access_token``). Never logs or raises the token."""
    value = json.dumps({"refresh_token": refresh_token}).encode()
    try:
        new_ref = await store.put(name, value)
    except ValueError:
        # a store that never overwrites a name (the development store): a fresh name beside it
        try:
            new_ref = await store.put(f"{name}/r-{secrets.token_hex(6)}", value)
        except Exception:  # noqa: BLE001 - keep the old token; the next refresh may refuse it
            return
    except Exception:  # noqa: BLE001 - see above
        return
    if new_ref == old_ref:
        return
    if not await on_rotated(new_ref):
        await store.destroy(new_ref)  # the connection ended meanwhile: keep no live token
        raise ConnectorRefused("connection_gone")
    # the old version is superseded: a failure to destroy it never fails the sync
    with contextlib.suppress(Exception):
        await store.destroy(old_ref)


async def disconnect(
    s: Any, *, connection_id: UUID, user: str, oauth: OAuthClient, store: SecretStore, now: datetime
) -> str:
    """Revoke upstream where possible, destroy the stored token, end the connection. Returns
    the upstream outcome (``ok`` or ``error``: a failed revocation is logged, never hidden)."""
    conn = (
        await s.execute(
            text("SELECT kind, token_ref FROM connector_connections WHERE id = :c"),
            {"c": connection_id},
        )
    ).first()
    if conn is None:
        raise ConnectorRefused("not_found")
    outcome = "ok"
    if conn.token_ref:
        try:
            secret = json.loads(await store.read(conn.token_ref))
            await oauth.revoke(conn.kind, secret["refresh_token"])
        except Exception:  # noqa: BLE001 - the local purge still happens and is logged
            outcome = "error"
        await store.destroy(conn.token_ref)
    await s.execute(
        text(
            "UPDATE connector_connections SET status = 'disconnected', token_ref = NULL,"
            " broken_reason = NULL, version = version + 1 WHERE id = :c"
        ),
        {"c": connection_id},
    )
    await access_log.log(
        s,
        connection_id=connection_id,
        principal=user,
        kind=conn.kind,
        purpose="revoke",
        outcome=outcome,
        at=now,
    )
    return outcome
