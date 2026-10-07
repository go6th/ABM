"""Tenant membership: add, change role, suspend, reactivate, remove (ID2, ID4).

Every change is a conditional update guarded by ``version`` (or an INSERT) plus a
``membership_events`` row in the same transaction. The two refusal rules come from
``identity.roles``: nobody changes their own membership, and the last active admin cannot lose
admin. The active-admin count is
taken under row locks on the admin rows, so two admins demoting each other at the same time
cannot leave the tenant without one (the second waits, re-counts and is refused).

The authorisation layer reads membership through ``api.membership.MembershipCache`` (about 60 s,
ARCH); ``active_role`` is its database source: the role of an ACTIVE member of a tenant that is
not suspended, else None.

GUARANTEE(test_membership_changes_follow_the_role_rules_under_races)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from identity.roles import is_role, role_change_refusal

ACTIVE_ROLE_SQL = (
    "SELECT m.role FROM memberships m WHERE m.user_id = :u AND m.status = 'active'"
    " AND NOT COALESCE((SELECT e.event = 'suspended' FROM tenant_events e"
    " WHERE e.event IN ('suspended', 'reactivated') ORDER BY e.seq DESC LIMIT 1), false)"
)


class MembershipRefused(PermissionError):
    """A change the rules refuse; ``reason`` is a fixed code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MembershipConflict(RuntimeError):
    """The membership changed since it was read (version mismatch); re-read and retry."""


@dataclass(frozen=True)
class Member:
    """One membership as stored."""

    user_id: str
    role: str
    status: str
    version: int


async def active_role(s: AsyncSession, user_id: str) -> str | None:
    """The role of an active member of a non-suspended context tenant, else None."""
    role: str | None = (await s.execute(text(ACTIVE_ROLE_SQL), {"u": user_id})).scalar_one_or_none()
    return role


async def get(s: AsyncSession, user_id: str) -> Member | None:
    """One membership of the context tenant."""
    row: Any = (
        await s.execute(
            text("SELECT user_id, role, status, version FROM memberships WHERE user_id = :u"),
            {"u": user_id},
        )
    ).first()
    return None if row is None else Member(row.user_id, row.role, row.status, int(row.version))


async def _event(
    s: AsyncSession,
    user_id: str,
    kind: str,
    now: datetime,
    actor: str,
    *,
    role: str | None = None,
    prior: str | None = None,
    reason: str | None = None,
) -> None:
    await s.execute(
        text(
            "INSERT INTO membership_events (user_id, event, role, prior_role, actor, reason, at)"
            " VALUES (:u, :e, :r, :p, :a, :why, :at)"
        ),
        {"u": user_id, "e": kind, "r": role, "p": prior, "a": actor, "why": reason, "at": now},
    )


async def add(
    s: AsyncSession,
    *,
    user_id: str,
    role: str,
    actor: str,
    now: datetime,
    email: str | None = None,
    display_name: str | None = None,
    provisioned: bool = False,
) -> Member:
    """Add a member (and its user row). MembershipRefused when they already exist."""
    if not is_role(role):
        raise MembershipRefused("unknown_role")
    if actor == user_id and not provisioned:
        raise MembershipRefused("self_change")
    await s.execute(
        text(
            "INSERT INTO users (subject, email, display_name, created_at) VALUES (:u, :e, :d, :at)"
            " ON CONFLICT (tenant_id, subject) DO NOTHING"
        ),
        {"u": user_id, "e": email, "d": display_name, "at": now},
    )
    inserted = (
        await s.execute(
            text(
                "INSERT INTO memberships (user_id, role, tenant_id, status, version, updated_at)"
                " VALUES (:u, :r, app_tenant(), 'active', 1, :at)"
                " ON CONFLICT (tenant_id, user_id) DO NOTHING RETURNING user_id"
            ),
            {"u": user_id, "r": role, "at": now},
        )
    ).first()
    if inserted is None:
        raise MembershipRefused("already_member")
    await _event(s, user_id, "provisioned" if provisioned else "added", now, actor, role=role)
    return Member(user_id, role, "active", 1)


async def _locked_active_admins(s: AsyncSession) -> int:
    n: int = (
        await s.execute(
            text(
                "SELECT count(*) FROM (SELECT 1 FROM memberships"
                " WHERE role = 'admin' AND status = 'active' FOR UPDATE) admins"
            )
        )
    ).scalar_one()
    return int(n)


async def change(
    s: AsyncSession,
    *,
    actor: str,
    user_id: str,
    expected_version: int,
    now: datetime,
    new_role: str | None = None,
    new_status: str | None = None,
    reason: str | None = None,
) -> Member:
    """Change a member's role and/or status under the rules; returns the new membership."""
    current = await get(s, user_id)
    if current is None:
        raise MembershipRefused("not_a_member")
    if current.status == "removed":
        raise MembershipRefused("removed")
    status = new_status or current.status
    admins = await _locked_active_admins(s)
    refusal = role_change_refusal(
        actor=actor,
        target=user_id,
        target_role=current.role,
        new_role=new_role,
        new_status=status,
        active_admins=admins if current.status == "active" else admins + 1,
    )
    if refusal:
        raise MembershipRefused(refusal)
    role = new_role or current.role
    row = (
        await s.execute(
            text(
                "UPDATE memberships SET role = :r, status = :st, version = version + 1,"
                " updated_at = :at WHERE user_id = :u AND version = :v RETURNING version"
            ),
            {"r": role, "st": status, "at": now, "u": user_id, "v": expected_version},
        )
    ).first()
    if row is None:
        raise MembershipConflict("membership changed since it was read")
    if role != current.role:
        await _event(
            s, user_id, "role_changed", now, actor, role=role, prior=current.role, reason=reason
        )
    if status != current.status:
        kind = {"active": "reactivated", "suspended": "suspended", "removed": "removed"}[status]
        await _event(s, user_id, kind, now, actor, role=role, reason=reason)
    return Member(user_id, role, status, int(row.version))


async def listing(s: AsyncSession, limit: int = 500) -> list[Member]:
    """The context tenant's members (bounded)."""
    rows = (
        await s.execute(
            text(
                "SELECT user_id, role, status, version FROM memberships ORDER BY user_id LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [Member(r.user_id, r.role, r.status, int(r.version)) for r in rows]
