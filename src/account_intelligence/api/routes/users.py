"""Tenant users and memberships (``/v1/users``; ID2, ID4). Admins only, with a fresh step-up.

* ``GET /v1/users``: the tenant's members (identifiers, role, status, version).
* ``POST /v1/users``: add a member with a role (the user id is the IdP subject the connection
  will present, ``<connection slug>:<subject>``).
* ``PATCH /v1/users``: change one member's role and/or status, conditional on the version read;
  refused for the caller's own membership and for the last active admin. A role change or
  suspension ends the member's sessions (privilege change rotates: they sign in again).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from identity import membership, sessions

router = APIRouter(prefix="/users", tags=["users"])
register("GET", "/v1/users", tenant_route("manage_users", "read", step_up=False))
register("POST", "/v1/users", tenant_route("manage_users", "admin"))
register("PATCH", "/v1/users", tenant_route("manage_users", "admin"))

Role = Literal["admin", "analyst", "seller", "viewer"]


class AddBody(BaseModel):
    """A new member. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=3, max_length=255, pattern=r"^[a-z0-9-]+:[^\s]{1,200}$")
    role: Role
    email: str | None = Field(default=None, max_length=320)
    display_name: str | None = Field(default=None, max_length=200)


class ChangeBody(BaseModel):
    """A membership change at the version it was read."""

    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=3, max_length=255)
    expected_version: int = Field(ge=1)
    role: Role | None = None
    status: Literal["active", "suspended", "removed"] | None = None
    reason: str | None = Field(default=None, max_length=300)


def _json(m: membership.Member) -> dict[str, Any]:
    return {"user_id": m.user_id, "role": m.role, "status": m.status, "version": m.version}


@router.get("")
async def list_users(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """Every member of the caller's tenant."""
    async with tenant_txn(ident.tenant_id) as s:
        members = await membership.listing(s)
    return {"members": [_json(m) for m in members]}


@router.post("", status_code=201)
async def add_user(
    body: AddBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Add a member."""
    now = plane_of(request).clock.now()
    try:
        async with tenant_txn(ident.tenant_id) as s:
            added = await membership.add(
                s,
                user_id=body.user_id,
                role=body.role,
                actor=ident.user_id,
                now=now,
                email=body.email.lower() if body.email else None,
                display_name=body.display_name,
            )
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="member_add",
                resource_type="member",
                resource_id=body.user_id,
            )
    except membership.MembershipRefused as e:
        raise HTTPException(409, e.reason) from None
    return _json(added)


@router.patch("")
async def change_user(
    body: ChangeBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Change a member's role or status under the role rules."""
    if body.role is None and body.status is None:
        raise HTTPException(422, "nothing to change")
    now = plane_of(request).clock.now()
    try:
        async with tenant_txn(ident.tenant_id) as s:
            changed = await membership.change(
                s,
                actor=ident.user_id,
                user_id=body.user_id,
                expected_version=body.expected_version,
                now=now,
                new_role=body.role,
                new_status=body.status,
                reason=body.reason,
            )
            await sessions.revoke_all(s, body.user_id, "privilege_change", now)
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="member_change",
                resource_type="member",
                resource_id=body.user_id,
            )
    except membership.MembershipRefused as e:
        raise HTTPException(409, e.reason) from None
    except membership.MembershipConflict:
        raise HTTPException(409, "membership changed since it was read") from None
    return _json(changed)
