"""Mail and drive (screen 8): the signed-in user's own read-only connections.

* ``GET /v1/mail-and-drive``: whether the client allows it; per source (Gmail, Google Drive,
  Outlook, OneDrive, SharePoint, grouped by ``family`` google or microsoft) Connected / Not
  connected / Not available yet (no OAuth app registered) / Needs attention (the grant broke),
  the last sync, its outcome and error code, and the items matched to the user's accounts; and
  the older Mail and Drive cards (one source each).
* ``POST /v1/mail-and-drive/{kind}/connect``: start the consent for one source (403 when the
  client does not allow it, 409 ``not_available`` without an OAuth app); returns where to send
  the browser. The consent is bound to this tenant, this user and this connection (PKCE).
* ``GET /v1/mail-and-drive/callback``: finish the consent and go back to the page. A provider
  error (``?error=...``), a missing code or a refused exchange goes back with a fixed code only
  (``denied``, ``provider``, ``scope``, ``expired`` or ``consent``); provider text never
  reaches the URL, and the consent state is used up either way.
* ``POST /v1/mail-and-drive/{family}/sync``: "Sync now" of the caller's own active connections
  of one family (google or microsoft), at most once per two minutes per user and family (429
  with Retry-After); returns each source's state and counts.
* ``POST /v1/mail-and-drive/{family}/disconnect``: revoke at the source where it can, destroy
  the stored token and delete every item that connection synced (``mail``, ``drive`` or one
  source kind).

Everything here is the caller's own: another user's connection is never listed, started,
finished, synced or disconnected by them. No response carries a token or any mail or file
content beyond the counts. The SQL lives in ``connectors_client.lifecycle``.

GUARANTEE(test_mail_and_drive_connects_syncs_and_disconnects_per_user)
GUARANTEE(test_sync_now_is_the_callers_own_and_rate_limited)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.ratelimit import limit_user
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica.settings import client_settings
from connectors_client import framework, lifecycle, maildrive, oauth
from connectors_client.ports import TokenRevoked, TokenSet
from connectors_client.scopes import spec

router = APIRouter(prefix="/mail-and-drive", tags=["mail-and-drive"])
register("GET", "/v1/mail-and-drive", tenant_route("read_public", "read", step_up=False))
register(
    "POST",
    "/v1/mail-and-drive/{kind}/connect",
    tenant_route("read_public", "admin", step_up=False),
)
register("GET", "/v1/mail-and-drive/callback", tenant_route("read_public", "admin", step_up=False))
register(
    "POST",
    "/v1/mail-and-drive/{family}/sync",
    tenant_route("read_public", "admin", step_up=False),
)
register(
    "POST",
    "/v1/mail-and-drive/{family}/disconnect",
    tenant_route("read_public", "admin", step_up=False),
)

Kind = Literal["gmail", "outlook", "google_drive", "onedrive", "sharepoint"]
Family = Literal["mail", "drive"]
Provider = Literal["google", "microsoft"]
Target = Literal["mail", "drive", "gmail", "outlook", "google_drive", "onedrive", "sharepoint"]
FAMILY: dict[str, tuple[str, ...]] = {"mail": maildrive.MAIL_KINDS, "drive": maildrive.DRIVE_KINDS}
ORDER = ("gmail", "google_drive", "outlook", "onedrive", "sharepoint")

# the callback's codes (the page turns each into a sentence)
DENIED = {"access_denied", "consent_required", "interaction_required", "user_cancelled"}
PROVIDER = {"server_error", "temporarily_unavailable"}
SCOPE = {"invalid_scope"}
REFUSAL = {
    "provider_unavailable": "provider",
    "scope_mismatch": "scope",
    "no_refresh_token": "scope",
    "unknown_or_used_state": "expired",
}


def _apps(request: Request) -> dict[str, tuple[str, str]]:
    return lifecycle.wiring(request.app.state.container.extras).apps


def _oauth(request: Request) -> Any:
    return lifecycle.wiring(request.app.state.container.extras).oauth


def _redirect_uri(request: Request) -> str:
    base = plane_of(request).base_url
    if not base:
        raise APIProblem(503, "not_configured", "Not configured", "No public base URL is set.")
    return f"{base.rstrip('/')}/v1/mail-and-drive/callback"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _status(row: dict[str, Any] | None, available: bool) -> str:
    if row is not None and row["status"] == "active":
        return "connected"
    if row is not None:
        return "broken"
    return "not_connected" if available else "not_available"


def _sync_fields(row: dict[str, Any] | None) -> dict[str, Any]:
    keys = ("broken_reason", "last_sync_status", "last_error", "last_sync_listed")
    out: dict[str, Any] = {k: (row or {}).get(k) for k in (*keys, "last_sync_matched")}
    out["last_error_at"] = _iso((row or {}).get("last_error_at"))
    return out


def _source(kind: str, row: dict[str, Any] | None, apps: dict[str, Any]) -> dict[str, Any]:
    available = spec(kind).provider in apps
    return {
        "kind": kind,
        "label": maildrive.LOCATION[kind],
        "family": lifecycle.family_of(kind),
        "group": lifecycle.group_of(kind),
        "available": available,
        "status": _status(row, available),
        "accounts": row["accounts"] if row else 0,
        "items": row["items"] if row else 0,
        "last_synced_at": _iso(row["last_synced_at"]) if row else None,
        **_sync_fields(row),
    }


async def _family(
    s: Any, user: str, family: str, rows: dict[str, dict[str, Any]], apps: dict[str, Any]
) -> dict[str, Any]:
    kinds = FAMILY[family]
    live = [rows[k] for k in kinds if k in rows]
    conn = max(live, key=lambda r: r["created_at"]) if live else None
    accounts, items = await lifecycle.family_counts(s, user, kinds)
    available = [k for k in kinds if spec(k).provider in apps]
    return {
        "status": _status(conn, bool(available)),
        "kind": conn["kind"] if conn else None,
        "source": maildrive.LOCATION[conn["kind"]] if conn else None,
        "options": [
            {"kind": k, "label": maildrive.LOCATION[k], "available": k in available} for k in kinds
        ],
        "accounts": accounts,
        "items": items,
        "last_synced_at": _iso(conn["last_synced_at"]) if conn else None,
        **_sync_fields(conn),
    }


@router.get("", response_model=C.MailAndDrive)
async def mail_and_drive(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The caller's sources, grouped by family, and the Mail and Drive cards."""
    apps = _apps(request)
    async with tenant_txn(ident.tenant_id) as s:
        allowed = (await client_settings(s)).mail_drive_allowed
        rows = await lifecycle.sources(s, ident.user_id, maildrive.KINDS)
        return {
            "allowed": allowed,
            "mail": await _family(s, ident.user_id, "mail", rows, apps),
            "drive": await _family(s, ident.user_id, "drive", rows, apps),
            "sources": [_source(k, rows.get(k), apps) for k in ORDER],
        }


async def _require_allowed(s: Any) -> None:
    if not (await client_settings(s)).mail_drive_allowed:
        raise APIProblem(
            403,
            "mail_drive_not_allowed",
            "Mail and drive is turned off",
            "Your administrator has turned off mail and drive connections.",
        )


@router.post("/{kind}/connect", response_model=C.MailDriveConnect)
async def connect(
    kind: Kind, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Start the read-only consent for one source."""
    apps = _apps(request)
    plane = plane_of(request)
    async with tenant_txn(ident.tenant_id) as s:
        await _require_allowed(s)
        if spec(kind).provider not in apps:
            raise APIProblem(
                409,
                "not_available",
                "Not available yet",
                f"Connecting {maildrive.LOCATION[kind]} is not available yet.",
            )
        try:
            started = await oauth.start(
                s,
                kind=kind,
                user=ident.user_id,
                client_id=apps[str(spec(kind).provider)][0],
                redirect_uri=_redirect_uri(request),
                now=plane.clock.now(),
                personal=True,
            )
        except oauth.ConnectorRefused as refused:
            raise APIProblem(
                409, refused.reason, "Not available", "This source is turned off."
            ) from None
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="maildrive_consent",
            resource_type="connector",
            resource_id=started.connection_id,
        )
    return {"authorize_url": started.authorize_url}


class _Declined:
    """The ``OAuthClient`` for a consent the provider did not complete: the exchange is refused
    without any call out, so ``oauth.complete`` uses up the state and fails closed."""

    async def exchange(
        self, kind: str, *, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet:
        """Refused: there is no code to exchange."""
        raise TokenRevoked(f"{kind}: the consent was not completed")

    async def refresh(self, kind: str, refresh_token: str) -> TokenSet:
        """Never used."""
        raise TokenRevoked(kind)

    async def revoke(self, kind: str, token: str) -> None:
        """Never used."""
        raise TokenRevoked(kind)


def _provider_code(error: str) -> str:
    if error in DENIED:
        return "denied"
    if error in PROVIDER:
        return "provider"
    if error in SCOPE:
        return "scope"
    return "consent"


def _back(code: str | None = None) -> Response:
    return RedirectResponse("/mail-and-drive" + (f"?error={code}" if code else ""), status_code=303)


async def _complete(
    request: Request,
    ident: Identity,
    state: str,
    code: str,
    client: Any,
    audit: Callable[[Any, UUID], Awaitable[None]],
) -> tuple[UUID | None, str | None]:
    """``oauth.complete`` in its own transaction: (the connection, None) or (None, the refusal
    reason). A refusal commits the used-up state (a consent is never retried), except one bound
    to another user, which rolls back so its owner can still finish."""
    plane = plane_of(request)
    try:
        async with tenant_txn(ident.tenant_id) as s:
            try:
                connection = await oauth.complete(
                    s,
                    state=state,
                    code=code,
                    user=ident.user_id,
                    redirect_uri=_redirect_uri(request),
                    oauth=client,
                    store=plane.secrets,
                    tenant_id=ident.tenant_id,
                    now=plane.clock.now(),
                )
            except oauth.ConnectorRefused as refused:
                if refused.reason == "state_bound_to_another_user":
                    raise
                return None, refused.reason
            await audit(s, connection)  # in the same transaction as the connection
            return connection, None
    except oauth.ConnectorRefused as refused:
        return None, refused.reason


@router.get("/callback")
async def callback(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,  # accepted and never used: provider text stays out
    ident: Identity = Depends(authorize),
) -> Response:
    """Finish a consent (bound to this user) and return to the page."""
    del error_description

    async def audit(s: Any, connection: UUID) -> None:
        """The new connection in the tenant's audit log (admin class)."""
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="maildrive_connected",
            resource_type="connector",
            resource_id=connection,
        )

    usable = state if state is not None and 16 <= len(state) <= 200 else None
    if error is not None or not code or len(code) > 2000:
        # the provider did not complete the consent: use up the state, never call out
        reason = None
        if usable is not None:
            try:
                _, reason = await _complete(request, ident, usable, "declined", _Declined(), audit)
            except APIProblem:
                reason = None  # no public base URL: the state simply expires
        if error is not None:
            return _back(_provider_code(error))
        return _back("expired" if reason == "unknown_or_used_state" else "consent")
    if usable is None:
        return _back("expired")
    try:
        _, reason = await _complete(request, ident, usable, code, _oauth(request), audit)
    except httpx.HTTPError:
        return _back("provider")  # the provider did not answer (5xx or no connection)
    return _back(None if reason is None else REFUSAL.get(reason, "consent"))


@router.post("/{family}/sync", response_model=C.MailDriveSynced)
async def sync_now(
    family: Provider, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Sync the caller's own active connections of one family now."""
    plane = plane_of(request)
    wired = lifecycle.wiring(request.app.state.container.extras)
    async with tenant_txn(ident.tenant_id) as s:
        await _require_allowed(s)
        conns = await lifecycle.owner_connections(
            s,
            owner_user_id=ident.user_id,
            kinds=lifecycle.family_kinds(family),
            statuses=("active",),
        )
    if not conns:
        raise APIProblem(
            409, "not_connected", "Nothing to sync", "Connect a source first, then sync it."
        )
    await limit_user(ident, f"maildrive_sync_{family}")
    done = await lifecycle.sync_sources(
        tenant_txn, ident.tenant_id, conns, wired=wired, store=plane.secrets, now=plane.clock.now()
    )
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="maildrive_sync_now",
            resource_type="connector",
            resource_id=conns[0][0],
        )
    return {
        "family": family,
        "sources": [
            {
                "kind": d.kind,
                "label": maildrive.LOCATION[d.kind],
                "state": d.state,
                "listed": d.listed,
                "matched": d.matched,
                "reason": d.reason,
            }
            for d in done
        ],
    }


@router.post("/{family}/disconnect", response_model=C.MailDriveDisconnected)
async def disconnect(
    family: Target, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Revoke, destroy the token and delete what the caller's connection synced (``mail``,
    ``drive`` or one source kind)."""
    plane = plane_of(request)
    kinds = FAMILY.get(family, (family,))
    deleted = 0
    async with tenant_txn(ident.tenant_id) as s:
        conns = await lifecycle.owner_connections(s, owner_user_id=ident.user_id, kinds=kinds)
        ids: list[UUID] = [cid for cid, _ in conns]
        for connection_id in ids:
            await oauth.disconnect(
                s,
                connection_id=connection_id,
                user=ident.user_id,
                oauth=_oauth(request),
                store=plane.secrets,
                now=plane.clock.now(),
            )
            deleted += await framework.delete_connection_items(s, connection_id)
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="maildrive_disconnected",
            resource_type="connector",
            resource_id=ids[0] if ids else None,
        )
    return {"disconnected": len(ids), "items_deleted": deleted}
