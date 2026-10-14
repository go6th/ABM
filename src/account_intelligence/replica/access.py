"""Idle expiry (admin console A3 and A4): a user who has not used the workspace for the client's
idle days loses access; three days before, they are told in the workspace. History is kept and
an admin can re-enable them in one click (``identity.membership.reenable``).

Activity is any signed-in use (``replica.settings.touch_activity``: each page load and each
question). A member who never signed in has no activity yet and is not expired: they are still an
invite. Ending access goes through ``identity.membership.end_access`` (so every sign-in method and
every API call refuses them) and ends their sessions.

``expire_idle`` runs in one client's transaction; ``expire_everywhere`` loops over the clients,
each in its own transaction, and one client's failure never stops the others.

GUARANTEE(test_idle_users_are_warned_then_expired_and_history_is_kept)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.replica.admin import WARN_DAYS, days_between
from account_intelligence.replica.notifications import notify
from account_intelligence.replica.settings import client_settings
from identity import membership, sessions

ACTOR = "system:idle_expiry"


@dataclass
class ExpiryReport:
    """What one pass did, in counts (and the clients it could not check)."""

    clients: int = 0
    warned: int = 0
    expired: int = 0
    failed: list[str] = field(default_factory=list)

    def line(self) -> str:
        """One log line."""
        return (
            f"clients={self.clients} warned={self.warned} expired={self.expired}"
            f" failed={len(self.failed)}"
        )


async def expire_idle(s: Any, now: datetime) -> tuple[int, int]:
    """Warn and expire the idle users of the caller's client; returns (warned, expired)."""
    days = (await client_settings(s)).idle_expiry_days
    rows = (
        await s.execute(
            text(
                "SELECT m.user_id, m.last_active_at,"
                " EXISTS (SELECT 1 FROM notifications n WHERE n.user_id = m.user_id"
                "   AND n.kind = 'access_expiring' AND n.created_at >= m.last_active_at) AS warned"
                " FROM memberships m WHERE m.status = 'active' AND m.access_state = 'active'"
                " AND m.last_active_at IS NOT NULL"
            )
        )
    ).all()
    warned = expired = 0
    for r in rows:
        idle = days_between(r.last_active_at, now)
        if idle >= days:
            if await membership.end_access(s, r.user_id, "expired", ACTOR, now):
                await sessions.revoke_all(s, r.user_id, "access_expired", now)
                expired += 1
        elif idle >= days - WARN_DAYS and not r.warned:
            await notify(s, user_id=r.user_id, kind="access_expiring")
            warned += 1
    return warned, expired


async def expire_everywhere(plain_factory: Any, txn: Any, now: datetime) -> ExpiryReport:
    """One pass over every client (identifiers from ``operator_tenant_overview``)."""
    report = ExpiryReport()
    async with plain_factory() as plain:
        ids = [
            UUID(str(r.tenant_id))
            for r in await plain.execute(text("SELECT tenant_id FROM operator_tenant_overview()"))
        ]
    # LOOP(idle expiry): cap=one pass over the clients; exit=every client checked or failed
    for tid in ids:
        try:
            async with txn(tid) as s:
                warned, expired = await expire_idle(s, now)
        except Exception:  # one client's failure never stops the others; reported by id
            report.failed.append(str(tid))
            continue
        report.clients += 1
        report.warned += warned
        report.expired += expired
    return report
