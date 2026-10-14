"""Mail and drive (screen 8): the signed-in user's own read-only connections.

* ``GET /v1/mail-and-drive``: whether the client allows it, and for Mail (Gmail or Outlook) and
  Drive (Google Drive, OneDrive or SharePoint): Connected / Not connected / Not available yet
  (no OAuth app registered) / Needs reconnecting (the grant was revoked), the accounts and items
  matched, the last sync, and that it is shown only to the user.
* ``POST /v1/mail-and-drive/{kind}/connect``: start the consent for one source (403 when the
  client does not allow it, 409 ``not_available`` without an OAuth app); returns where to send
  the browser. The consent is bound to this tenant, this user and this connection (PKCE).
* ``GET /v1/mail-and-drive/callback``: finish the consent and go back to the page.
* ``POST /v1/mail-and-drive/{family}/disconnect``: revoke at the source where it can, destroy
  the stored token and delete every item that connection synced.

Everything here is the caller's own: another user's connection is never listed, started,
finished or disconnected by them. No response carries a token or any mail or file content beyond
the counts.

GUARANTEE(test_mail_and_drive_connects_syncs_and_disconnects_per_user)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica.settings import client_settings
from connectors_client import framework, maildrive, oauth
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
    "/v1/mail-and-drive/{family}/disconnect",
    tenant_route("read_public", "admin", step_up=False),
)

Kind = Literal["gmail", "outlook", "google_drive", "onedrive", "sharepoint"]
Family = Literal["mail", "drive"]
FAMILY: dict[str, tuple[str, ...]] = {"mail": maildrive.MAIL_KINDS, "drive": maildrive.DRIVE_KINDS}


def _apps(request: Request) -> dict[str, tuple[str, str]]:
    return dict(request.app.state.container.extras.get("maildrive_apps") or {})


def _oauth(request: Request) -> Any:
    found = request.app.state.container.extras.get("maildrive_oauth")
    return found if found is not None else maildrive.HttpOAuthClient(_apps(request))


def _redirect_uri(request: Request) -> str:
    base = plane_of(request).base_url
    if not base:
        raise APIProblem(503, "not_configured", "Not configured", "No public base URL is set.")
    return f"{base.rstrip('/')}/v1/mail-and-drive/callback"


async def _family(s: Any, user: str, family: str, apps: dict[str, Any]) -> dict[str, Any]:
    kinds = FAMILY[family]
    conn = (
        await s.execute(
            text(
                "SELECT id, kind, status, last_synced_at FROM connector_connections"
                " WHERE owner_user_id = :u AND kind = ANY(:k) AND status IN ('active', 'broken')"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"u": user, "k": list(kinds)},
        )
    ).first()
    counts = (
        await s.execute(
            text(
                "SELECT count(DISTINCT i.account_id) AS accounts,"
                " count(DISTINCT i.resource_id) AS items FROM connector_items i"
                " JOIN connector_connections c ON c.id = i.connection_id"
                " WHERE i.owner_user_id = :u AND i.state = 'matched' AND c.kind = ANY(:k)"
            ),
            {"u": user, "k": list(kinds)},
        )
    ).one()
    available = [k for k in kinds if spec(k).provider in apps]
    if conn is not None and conn.status == "active":
        status = "connected"
    elif conn is not None:
        status = "broken"
    else:
        status = "not_connected" if available else "not_available"
    return {
        "status": status,
        "kind": conn.kind if conn is not None else None,
        "source": maildrive.LOCATION[conn.kind] if conn is not None else None,
        "options": [
            {"kind": k, "label": maildrive.LOCATION[k], "available": k in available} for k in kinds
        ],
        "accounts": int(counts.accounts or 0),
        "items": int(counts.items or 0),
        "last_synced_at": conn.last_synced_at.isoformat()
        if conn is not None and conn.last_synced_at
        else None,
    }


@router.get("", response_model=C.MailAndDrive)
async def mail_and_drive(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The caller's Mail and Drive cards."""
    apps = _apps(request)
    async with tenant_txn(ident.tenant_id) as s:
        allowed = (await client_settings(s)).mail_drive_allowed
        return {
            "allowed": allowed,
            "mail": await _family(s, ident.user_id, "mail", apps),
            "drive": await _family(s, ident.user_id, "drive", apps),
        }


@router.post("/{kind}/connect", response_model=C.MailDriveConnect)
async def connect(
    kind: Kind, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Start the read-only consent for one source."""
    apps = _apps(request)
    plane = plane_of(request)
    async with tenant_txn(ident.tenant_id) as s:
        if not (await client_settings(s)).mail_drive_allowed:
            raise APIProblem(
                403,
                "mail_drive_not_allowed",
                "Mail and drive is turned off",
                "Your administrator has turned off mail and drive connections.",
            )
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


@router.get("/callback")
async def callback(
    request: Request,
    state: str = Query(min_length=16, max_length=200),
    code: str = Query(min_length=1, max_length=2000),
    ident: Identity = Depends(authorize),
) -> Response:
    """Finish a consent (bound to this user) and return to the page."""
    plane = plane_of(request)
    try:
        async with tenant_txn(ident.tenant_id) as s:
            connection = await oauth.complete(
                s,
                state=state,
                code=code,
                user=ident.user_id,
                redirect_uri=_redirect_uri(request),
                oauth=_oauth(request),
                store=plane.secrets,
                tenant_id=ident.tenant_id,
                now=plane.clock.now(),
            )
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="maildrive_connected",
                resource_type="connector",
                resource_id=connection,
            )
    except oauth.ConnectorRefused:
        return RedirectResponse("/mail-and-drive?error=consent", status_code=303)
    return RedirectResponse("/mail-and-drive", status_code=303)


@router.post("/{family}/disconnect", response_model=C.MailDriveDisconnected)
async def disconnect(
    family: Family, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Revoke, destroy the token and delete what the caller's connection synced."""
    plane = plane_of(request)
    deleted = 0
    async with tenant_txn(ident.tenant_id) as s:
        ids = [
            UUID(str(r[0]))
            for r in await s.execute(
                text(
                    "SELECT id FROM connector_connections WHERE owner_user_id = :u"
                    " AND kind = ANY(:k) AND status IN ('active', 'broken', 'pending')"
                ),
                {"u": ident.user_id, "k": list(FAMILY[family])},
            )
        ]
        for connection_id in ids:
            await oauth.disconnect(
                s,
                connection_id=connection_id,
                user=ident.user_id,
                oauth=_oauth(request),
                store=plane.secrets,
                now=datetime.now(UTC),
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
