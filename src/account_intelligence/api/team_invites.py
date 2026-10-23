"""Invite a person into one workspace, safely: shared by a client admin's Teammates page
(``POST /v1/users/invite``) and the operator console (``POST /operator/v1/console/clients/
{tenant_id}/users``).

* An address that already has an active account is added and signs in with its own password. No
  link is made: an invite link sets the password, so one for an existing identity would hand that
  account to whoever holds the link.
* A new address gets a 24-hour invite link, emailed when a mailer is set up and always returned
  to copy.
* An address whose invite is still waiting (from any workspace) is refused until it is accepted,
  for the same reason: a new link would set the password of an identity others already invited.
* The seat is taken first and given back on any later failure, so a full workspace never leaves a
  new identity behind.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from account_intelligence.api.errors import APIProblem
from account_intelligence.db.session import tenant_txn, untenanted_session
from identity import local_password, membership
from metering import limits

INVITE_HOURS = 24  # the database refuses longer invite tokens

Audit = Callable[[Any, str], Awaitable[None]]


async def team_members(tenant_id: UUID, caller: str) -> list[dict[str, Any]]:
    """The workspace's members (not removed) with email and name; ``pending`` while an invited
    person has not accepted, ``you`` for the caller."""
    async with tenant_txn(tenant_id) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT m.user_id, m.role, m.status, m.version, u.email, u.display_name"
                    " FROM memberships m LEFT JOIN users u ON u.subject = m.user_id"
                    " WHERE m.status <> 'removed' ORDER BY lower(coalesce(u.email, m.user_id))"
                    " LIMIT 500"
                )
            )
        ).all()
    waiting: set[str] = set()
    async with untenanted_session() as plain:
        for r in rows:
            if r.email and str(r.user_id).startswith("local:"):
                found = (
                    await plain.execute(
                        text("SELECT status FROM local_auth_lookup(:e)"), {"e": r.email}
                    )
                ).first()
                if found is not None and found.status == "pending_verification":
                    waiting.add(str(r.user_id))
    return [
        {
            "user_id": str(r.user_id),
            "email": r.email,
            "display_name": r.display_name,
            "role": r.role,
            "status": r.status,
            "version": int(r.version),
            "pending": str(r.user_id) in waiting,
            "you": str(r.user_id) == caller,
        }
        for r in rows
    ]


async def invite_member(
    plane: Any, tenant_id: UUID, *, email: str, role: str, actor: str, audit: Audit
) -> dict[str, Any]:
    """Invite ``email`` into ``tenant_id`` with ``role``; ``audit(session, member_id)`` records it
    inside the membership transaction. Returns the TeamInvite body."""
    now = plane.clock.now()
    email = email.strip().lower()
    url: str | None = None
    expires: str | None = None
    async with untenanted_session() as plain:
        found = (
            await plain.execute(text("SELECT * FROM local_auth_lookup(:e)"), {"e": email})
        ).first()
    if found is not None and found.status == "pending_verification":
        raise APIProblem(
            409,
            "invite_pending_elsewhere",
            "Invite waiting",
            "This person has an invitation waiting. Once they accept it, invite them again.",
        )
    if found is not None and found.status != "active":
        raise APIProblem(409, "invite_refused", "Invite refused", "This address cannot be invited.")
    # the seat first, so a full workspace never leaves a new identity behind
    async with tenant_txn(tenant_id) as s:
        try:
            await limits.consume(s, "seats", now)
        except limits.LimitReached as e:
            raise limits.limit_problem(e) from None
    try:
        existing = found is not None
        if found is not None:
            identity_id = found.user_id
        else:
            base = plane.base_url
            if not base:
                raise APIProblem(
                    503, "not_configured", "Not configured", "No public base URL is set."
                )
            token = secrets.token_urlsafe(32)
            until = now + timedelta(hours=INVITE_HOURS)
            unusable = await local_password.hash_password(secrets.token_urlsafe(32), email=email)
            async with untenanted_session() as plain:
                identity_id = await plain.scalar(
                    text("SELECT local_auth_create(:e, :h, :now)"),
                    {"e": email, "h": unusable, "now": now},
                )
                issued = await plain.scalar(
                    text("SELECT local_auth_issue_token(:u, 'invite_acceptance', :h, :x, :now)"),
                    {
                        "u": identity_id,
                        "h": hashlib.sha256(token.encode()).hexdigest(),
                        "x": until,
                        "now": now,
                    },
                )
            if not issued:
                raise APIProblem(
                    409, "invite_refused", "Invite refused", "This address cannot be invited."
                )
            url = f"{base.rstrip('/')}/invite#{token}"
            expires = until.isoformat()
        try:
            async with tenant_txn(tenant_id) as s:
                await membership.add(
                    s,
                    user_id=f"local:{identity_id}",
                    role=role,
                    actor=actor,
                    now=now,
                    email=email,
                )
                await audit(s, f"local:{identity_id}")
                workspace = await s.scalar(
                    text("SELECT name FROM tenants WHERE id = :t"), {"t": tenant_id}
                )
        except membership.MembershipRefused as e:
            raise HTTPException(409, e.reason) from None
    except BaseException:  # any failure after the seat was taken gives it back, then re-raises
        async with tenant_txn(tenant_id) as s:
            await limits.release(s, "seats", now)
        raise
    emailed = False
    if url is not None and expires is not None:
        emailed = await email_invite(plane, email, str(workspace or "your company"), url, expires)
    return {
        "email": email,
        "role": role,
        "existing_account": existing,
        "invite_url": url,
        "expires_at": expires,
        "emailed": emailed,
    }


async def email_invite(plane: Any, email: str, workspace: str, url: str, expires: str) -> bool:
    """Email the invite when a mailer is set up; True when it was handed over."""
    from account_intelligence.platform.ports import NotConfigured
    from obs.logging import logger

    try:
        await plane.mailer.send(
            email,
            "client_invite",
            {"client_name": workspace, "invite_url": url, "expires": expires[:10]},
        )
    except NotConfigured:
        return False
    except Exception as error:  # noqa: BLE001 - the copyable link stays the fallback
        logger(__name__).warning(
            "invite_email_not_sent", extra={"error_class": type(error).__name__}
        )
        return False
    return True
