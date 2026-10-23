"""Tenant users and memberships (``/v1/users``; ID2, ID4). Admins only, with a fresh step-up.

* ``GET /v1/users``: the tenant's members (identifiers, role, status, version).
* ``POST /v1/users``: add a member with a role (the user id is the IdP subject the connection
  will present, ``<connection slug>:<subject>``).
* ``PATCH /v1/users``: change one member's role and/or status, conditional on the version read;
  refused for the caller's own membership and for the last active admin. A role change or
  suspension ends the member's sessions (privilege change rotates: they sign in again).
* ``GET /v1/users/team``: the members with their email and name, and whether each has accepted.
* ``POST /v1/users/invite``: invite a teammate by email (the rules: api.team_invites).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.team_invites import invite_member, team_members
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from identity import membership, sessions
from metering import limits

router = APIRouter(prefix="/users", tags=["users"])
register("GET", "/v1/users", tenant_route("manage_users", "read", step_up=False))
register("POST", "/v1/users", tenant_route("manage_users", "admin"))
register("PATCH", "/v1/users", tenant_route("manage_users", "admin"))
register("GET", "/v1/users/team", tenant_route("manage_users", "read", step_up=False))
register("POST", "/v1/users/invite", tenant_route("manage_users", "admin"))

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


class InviteBody(BaseModel):
    """A teammate to invite: their work email and role. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Role


def _json(m: membership.Member) -> dict[str, Any]:
    return {"user_id": m.user_id, "role": m.role, "status": m.status, "version": m.version}


@router.get("", response_model=C.Members)
async def list_users(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """Every member of the caller's tenant."""
    async with tenant_txn(ident.tenant_id) as s:
        members = await membership.listing(s)
    return {"members": [_json(m) for m in members]}


@router.post("", response_model=C.Member, status_code=201)
async def add_user(
    body: AddBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Add a member."""
    now = plane_of(request).clock.now()
    try:
        async with tenant_txn(ident.tenant_id) as s:
            try:
                await limits.consume(s, "seats", now)
            except limits.LimitReached as e:
                raise limits.limit_problem(e) from None
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


@router.patch("", response_model=C.Member)
async def change_user(
    body: ChangeBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Change a member's role or status under the role rules."""
    if body.role is None and body.status is None:
        raise HTTPException(422, "nothing to change")
    now = plane_of(request).clock.now()
    try:
        async with tenant_txn(ident.tenant_id) as s:
            current = await membership.get(s, body.user_id)
            before = current.status if current else None
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
            if body.status in ("suspended", "removed") and before == "active":
                await limits.release(s, "seats", now)
            elif body.status == "active" and before != "active":
                try:
                    await limits.consume(s, "seats", now)
                except limits.LimitReached as e:
                    raise limits.limit_problem(e) from None
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


@router.get("/team", response_model=C.Team)
async def team(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The members with email and name; ``pending`` while an invited person has not accepted."""
    return {"members": await team_members(ident.tenant_id, ident.user_id)}


@router.post("/invite", response_model=C.TeamInvite, status_code=201)
async def invite(
    body: InviteBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Invite a teammate (see account_intelligence.api.team_invites for when a link is made)."""

    async def audit(s: Any, member: str) -> None:
        """The invite in the tenant's audit log (admin class)."""
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="member_invite",
            resource_type="member",
            resource_id=member,
        )

    return await invite_member(
        plane_of(request),
        ident.tenant_id,
        email=body.email,
        role=body.role,
        actor=ident.user_id,
        audit=audit,
    )
