"""Client history connectors (``/v1/connectors``; CN1 to CN4, D-65). Admins only.

* ``GET /v1/connectors``: connections, their grants and how many items are held off limits.
* ``POST /v1/connectors`` {kind}: start a consent (fresh step-up); returns where to send the admin.
* ``GET /v1/connectors/oauth/callback``: finish the consent (the state is bound to this tenant,
  this admin and the connection; PKCE; exact scopes) and return to the connectors page.
* ``POST /v1/connectors/{id}/grants``: allow a Drive folder, a CRM object or a CRM field.
* ``POST /v1/connectors/{id}/fetch``: one bounded, read-only fetch pass into quarantine.
* ``GET /v1/connectors/{id}/quarantine``; ``POST .../quarantine/{item_id}/clear`` or ``/refuse``.
* ``POST /v1/connectors/{id}/disconnect``: revoke upstream where possible and purge the token.
No response ever carries a token; content fetched from a source is never returned here.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from connectors_client import framework, oauth, quarantine
from connectors_client.scopes import spec
from metering import limits

router = APIRouter(prefix="/connectors", tags=["connectors"])
register("GET", "/v1/connectors", tenant_route("manage_connectors", "read", step_up=False))
register("POST", "/v1/connectors", tenant_route("manage_connectors", "admin"))
register(
    "GET",
    "/v1/connectors/oauth/callback",
    tenant_route("manage_connectors", "admin", step_up=False),
)
register(
    "POST", "/v1/connectors/{connection_id}/grants", tenant_route("manage_connectors", "admin")
)
register(
    "POST",
    "/v1/connectors/{connection_id}/fetch",
    tenant_route("manage_connectors", "admin", step_up=False),
)
register(
    "GET",
    "/v1/connectors/{connection_id}/quarantine",
    tenant_route("manage_connectors", "read", step_up=False),
)
register(
    "POST",
    "/v1/connectors/{connection_id}/quarantine/{item_id}/{decision}",
    tenant_route("manage_connectors", "admin"),
)
register(
    "POST", "/v1/connectors/{connection_id}/disconnect", tenant_route("manage_connectors", "admin")
)

Kind = Literal["google_drive", "salesforce", "hubspot", "gmail", "call_notes"]


def _redirect_uri(request: Request) -> str:
    base = plane_of(request).base_url
    if not base:
        raise APIProblem(503, "not_configured", "Not configured", "No public base URL is set.")
    return f"{base.rstrip('/')}/v1/connectors/oauth/callback"


def _refused(e: oauth.ConnectorRefused) -> APIProblem:
    status = 404 if e.reason == "not_found" else 409
    return APIProblem(status, e.reason, "Connector refused", "The connector request was refused.")


@router.get("", response_model=C.Connections)
async def list_connectors(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The tenant's connections (never a token)."""
    async with tenant_txn(ident.tenant_id) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT c.id, c.kind, c.status, c.scopes, c.broken_reason, c.last_fetch_at,"
                    " coalesce((SELECT array_agg(g.grant_kind || ':' || g.resource_id ORDER BY"
                    " g.resource_id) FROM connector_grants g WHERE g.connection_id = c.id"
                    " AND g.revoked_at IS NULL), '{}') AS allowed,"
                    " (SELECT count(*) FROM connector_quarantine q WHERE q.connection_id = c.id"
                    " AND q.status = 'held') AS held FROM connector_connections c"
                    " ORDER BY c.created_at"
                )
            )
        ).all()
    return {
        "connections": [
            {
                "id": str(r.id),
                "kind": r.kind,
                "status": r.status,
                "scopes": list(r.scopes),
                "broken_reason": r.broken_reason,
                "allowed": list(r.allowed),
                "quarantined": int(r.held),
                "last_fetch_at": r.last_fetch_at.isoformat() if r.last_fetch_at else None,
            }
            for r in rows
        ]
    }


class StartBody(BaseModel):
    """A source to connect. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    kind: Kind


@router.post("", response_model=C.ConnectorStarted, status_code=201)
async def start(
    body: StartBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Start a consent for one source."""
    if spec(body.kind).family == "mail":
        # a mailbox is personal: only its owner connects it, on the Mail and drive page
        raise APIProblem(
            409,
            "personal_source",
            "Personal source",
            "Mail is connected by each person on the Mail and drive page.",
        )
    plane = plane_of(request)
    client_id = (
        plane.connector_client_id(body.kind) if body.kind not in ("gmail", "call_notes") else ""
    )
    try:
        async with tenant_txn(ident.tenant_id) as s:
            try:
                await limits.consume(s, "connectors", plane.clock.now())
            except limits.LimitReached as e:
                raise limits.limit_problem(e) from None
            started = await oauth.start(
                s,
                kind=body.kind,
                user=ident.user_id,
                client_id=client_id,
                redirect_uri=_redirect_uri(request),
                now=plane.clock.now(),
            )
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="connector_consent",
                resource_type="connector",
                resource_id=started.connection_id,
            )
    except oauth.ConnectorRefused as e:
        raise _refused(e) from None
    return {"connection_id": str(started.connection_id), "authorize_url": started.authorize_url}


@router.get("/oauth/callback")
async def callback(
    request: Request,
    state: str = Query(min_length=16, max_length=200),
    code: str = Query(min_length=1, max_length=2000),
    ident: Identity = Depends(authorize),
) -> Response:
    """Finish a consent and return to the connectors page."""
    plane = plane_of(request)
    client, _, _ = plane.connector_ports()
    try:
        async with tenant_txn(ident.tenant_id) as s:
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
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="connector_connected",
                resource_type="connector",
                resource_id=connection,
            )
    except oauth.ConnectorRefused as e:
        raise _refused(e) from None
    return RedirectResponse("/settings/connectors", status_code=303)


class GrantBody(BaseModel):
    """One allowed folder, object or field."""

    model_config = ConfigDict(extra="forbid")
    grant_kind: Literal["drive_folder", "crm_object", "crm_field"]
    resource_id: str = Field(min_length=1, max_length=300, pattern=r"^[A-Za-z0-9_.:-]+$")


@router.post("/{connection_id}/grants", response_model=C.ConnectorGranted, status_code=201)
async def grant(
    connection_id: UUID, body: GrantBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Allow one folder, object or field."""
    try:
        async with tenant_txn(ident.tenant_id) as s:
            await framework.add_grant(
                s,
                connection_id=connection_id,
                grant_kind=body.grant_kind,
                resource_id=body.resource_id,
                actor=ident.user_id,
                now=plane_of(request).clock.now(),
            )
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="connector_grant",
                resource_type="connector",
                resource_id=connection_id,
            )
    except framework.GrantRefused as e:
        raise APIProblem(
            404 if str(e) == "not_found" else 422, str(e), "Grant refused", "The grant was refused."
        ) from None
    return {"connection_id": str(connection_id), "granted": body.resource_id}


@router.post("/{connection_id}/fetch", response_model=C.FetchResult)
async def fetch(
    connection_id: UUID, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """One bounded fetch pass into quarantine (counts only; never content)."""
    plane = plane_of(request)
    client, reader, _ = plane.connector_ports()
    try:
        result = await framework.fetch_pass(
            tenant_txn,
            ident.tenant_id,
            connection_id,
            principal=ident.user_id,
            oauth=client,
            client=reader,
            store=plane.secrets,
            objects=plane.objects,
            now=plane.clock.now(),
        )
    except oauth.ConnectorRefused as e:
        raise _refused(e) from None
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="connector_fetch",
            resource_type="connector",
            resource_id=connection_id,
            outcome="ok" if not result.exit_reason.startswith("broken") else "denied",
        )
    return {
        "fetched": result.fetched,
        "held": result.held,
        "skipped": result.skipped,
        "pages": result.pages,
        "exit_reason": result.exit_reason,
    }


@router.get("/{connection_id}/quarantine", response_model=C.Quarantine)
async def held(
    connection_id: UUID, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Items surfaced outside the allow-list (identifiers and reasons only)."""
    async with tenant_txn(ident.tenant_id) as s:
        items = await quarantine.listing(s, connection_id)
    if items is None:  # L14: another client's connection is not found, never an empty list
        raise HTTPException(404, "connection not found")
    return {"items": items}


@router.post("/{connection_id}/quarantine/{item_id}/{decision}", response_model=C.QuarantineDecided)
async def decide(
    connection_id: UUID,
    item_id: UUID,
    decision: Literal["clear", "refuse"],
    request: Request,
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """Clear (a later pass may read it) or refuse one held item."""
    now = plane_of(request).clock.now()
    try:
        async with tenant_txn(ident.tenant_id) as s:
            resource = await quarantine.decide(
                s,
                item_id=item_id,
                connection_id=connection_id,
                decision="cleared" if decision == "clear" else "refused",
                actor=ident.user_id,
                now=now,
            )
            await audit_action(
                s,
                request,
                audit_class="admin",
                action=f"connector_{decision}",
                resource_type="connector_item",
                resource_id=item_id,
            )
    except quarantine.QuarantineError:
        raise HTTPException(404, "not found") from None
    return {"resource_id": resource, "status": "cleared" if decision == "clear" else "refused"}


@router.post("/{connection_id}/disconnect", response_model=C.Disconnected)
async def disconnect(
    connection_id: UUID, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Revoke upstream where possible, purge the token, end the connection."""
    plane = plane_of(request)
    client, _, _ = plane.connector_ports()
    try:
        async with tenant_txn(ident.tenant_id) as s:
            await limits.release(s, "connectors", plane.clock.now())
            upstream = await oauth.disconnect(
                s,
                connection_id=connection_id,
                user=ident.user_id,
                oauth=client,
                store=plane.secrets,
                now=plane.clock.now(),
            )
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="connector_disconnect",
                resource_type="connector",
                resource_id=connection_id,
            )
    except oauth.ConnectorRefused as e:
        raise _refused(e) from None
    return {
        "connection_id": str(connection_id),
        "status": "disconnected",
        "upstream_revocation": upstream,
    }
