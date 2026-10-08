"""Support grants, tenant side (``/v1/support``; AD3, D-66). Admins grant and revoke; the tenant
sees every support action.

* ``GET /v1/support/grants``; ``POST /v1/support/grants`` {grantee, scope, reason_code, minutes}
  (fresh step-up); ``POST /v1/support/grants/{grant_id}/revoke``.
* ``GET /v1/support/events``: every support action in this tenant (who, which route, outcome).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
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
from admin import support

router = APIRouter(prefix="/support", tags=["support"])
register("GET", "/v1/support/grants", tenant_route("grant_support", "read", step_up=False))
register("POST", "/v1/support/grants", tenant_route("grant_support", "admin"))
register(
    "POST",
    "/v1/support/grants/{grant_id}/revoke",
    tenant_route("grant_support", "admin", step_up=False),
)
register("GET", "/v1/support/events", tenant_route("read_audit", "read"))


class GrantBody(BaseModel):
    """A grant to one named 6th operator. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    grantee: str = Field(pattern=r"^[a-z0-9._@-]{1,120}$")
    scope: Literal["read", "write"] = "read"
    reason_code: str = Field(pattern=r"^[a-z_]{1,40}$")
    minutes: int = Field(ge=1, le=1440)


@router.get("/grants", response_model=C.SupportGrants)
async def grants(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The tenant's grants."""
    async with tenant_txn(ident.tenant_id) as s:
        return {"grants": await support.listing(s)}


@router.post("/grants", response_model=C.GrantCreated, status_code=201)
async def create(
    body: GrantBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Grant one operator time-boxed access."""
    try:
        async with tenant_txn(ident.tenant_id) as s:
            gid = await support.create(
                s,
                grantee=body.grantee,
                scope=body.scope,
                reason_code=body.reason_code,
                minutes=body.minutes,
                granted_by=ident.user_id,
                now=plane_of(request).clock.now(),
            )
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="support_grant",
                resource_type="support_grant",
                resource_id=gid,
            )
    except support.SupportRefused as e:
        raise APIProblem(422, e.reason, "Grant refused", "The support grant was refused.") from None
    return {"grant_id": str(gid)}


@router.post("/grants/{grant_id}/revoke", response_model=C.GrantRevoked)
async def revoke(
    grant_id: UUID, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """End a grant now."""
    async with tenant_txn(ident.tenant_id) as s:
        ended = await support.revoke(
            s, grant_id, by=ident.user_id, now=plane_of(request).clock.now()
        )
        if not ended:
            raise HTTPException(404, "not found")
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="support_revoke",
            resource_type="support_grant",
            resource_id=grant_id,
        )
    return {"grant_id": str(grant_id), "revoked": True}


@router.get("/events", response_model=C.SupportEvents)
async def events(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """Every support action in this tenant."""
    async with tenant_txn(ident.tenant_id) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT grant_id, operator, method, route, outcome, at"
                    " FROM support_session_events"
                    " ORDER BY id DESC LIMIT 500"
                )
            )
        ).all()
    return {
        "events": [
            {
                "grant_id": str(r.grant_id),
                "operator": r.operator,
                "method": r.method,
                "route": r.route,
                "outcome": r.outcome,
                "at": r.at.isoformat(),
            }
            for r in rows
        ]
    }
