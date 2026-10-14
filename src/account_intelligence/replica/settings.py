"""Client settings (limits per user and features) and the counts the user shell shows.

A client's row is created from the platform defaults the first time it is read, inside the
client's own transaction, so every client always has limits. The account cap counts the
accounts one user added (``accounts.added_by``); accounts added by the scheduler or copied in by
another user do not count against this user.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

CADENCE_ORDER = ("3d", "weekly", "2w", "monthly")
CADENCE_DAYS = {"3d": 3, "weekly": 7, "2w": 14, "monthly": 30}
CADENCE_LABEL = {
    "3d": "Every 3 days",
    "weekly": "Weekly",
    "2w": "Every 2 weeks",
    "monthly": "Monthly",
    "off": "Off",
}


@dataclass(frozen=True)
class ClientSettings:
    """One client's limits and features."""

    account_cap_per_user: int
    idle_expiry_days: int
    monthly_credit_pool: int
    fastest_schedule: str
    mail_drive_allowed: bool
    web_refresh_allowed: bool

    def allows_cadence(self, cadence: str) -> bool:
        """``off`` is always allowed; anything faster than the fastest allowed schedule is not."""
        if cadence == "off":
            return True
        if cadence not in CADENCE_ORDER:
            return False
        return CADENCE_ORDER.index(cadence) >= CADENCE_ORDER.index(self.fastest_schedule)


async def client_settings(session: Any) -> ClientSettings:
    """The caller tenant's settings; created from the platform defaults on first read."""
    row = (
        await session.execute(
            text(
                "SELECT account_cap_per_user, idle_expiry_days, monthly_credit_pool,"
                " fastest_schedule,"
                " mail_drive_allowed, web_refresh_allowed FROM client_settings"
            )
        )
    ).first()
    if row is None:
        await session.execute(
            text(
                "INSERT INTO client_settings (account_cap_per_user, idle_expiry_days,"
                " monthly_credit_pool,"
                " fastest_schedule, updated_by)"
                " SELECT account_cap_per_user, idle_expiry_days, monthly_credit_pool,"
                " fastest_schedule,"
                " 'platform_defaults' FROM platform_defaults WHERE id = 1"
                " ON CONFLICT (tenant_id) DO NOTHING"
            )
        )
        return await client_settings(session)
    return ClientSettings(
        account_cap_per_user=int(row.account_cap_per_user),
        idle_expiry_days=int(row.idle_expiry_days),
        monthly_credit_pool=int(row.monthly_credit_pool),
        fastest_schedule=str(row.fastest_schedule),
        mail_drive_allowed=bool(row.mail_drive_allowed),
        web_refresh_allowed=bool(row.web_refresh_allowed),
    )


async def accounts_used(session: Any, user_id: str) -> int:
    """How many accounts this user has added to the workspace."""
    value = await session.scalar(
        text("SELECT count(*) FROM accounts WHERE added_by = :u"), {"u": user_id}
    )
    return int(value or 0)


async def touch_activity(session: Any, user_id: str, now: Any) -> None:
    """Record that the user was active (idle expiry counts from here). At most once a minute."""
    await session.execute(
        text(
            "UPDATE memberships SET last_active_at = :now WHERE user_id = :u"
            " AND (last_active_at IS NULL OR last_active_at < CAST(:now AS timestamptz) - interval"
            " '1 minute')"
        ),
        {"u": user_id, "now": now},
    )
