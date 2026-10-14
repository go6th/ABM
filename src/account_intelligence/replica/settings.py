"""Client settings (limits per user and features) and the counts the user shell shows.

A client's row is created from the platform defaults the first time it is read, inside the
client's own transaction, so every client always has limits. The account cap counts the
accounts one user added (``accounts.added_by``); accounts added by the scheduler or copied in by
another user do not count against this user.

The operator plane (admin console A3, A7) changes a client's settings with ``update_client`` (in
that client's transaction) and the platform defaults with ``update_defaults``; a client created
in the console starts ``invited`` and becomes ``active`` at its first activity.
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
    # limits per user (owner decision 2026-10-14): the defaults for the client's users
    research_limit_default: int = 2
    chat_limit_default: int = 40
    limit_reset: str = "monthly"

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
                " fastest_schedule, mail_drive_allowed, web_refresh_allowed,"
                " research_limit_default, chat_limit_default, limit_reset FROM client_settings"
            )
        )
    ).first()
    if row is None:
        await session.execute(
            text(
                "INSERT INTO client_settings (account_cap_per_user, idle_expiry_days,"
                " monthly_credit_pool, fastest_schedule, research_limit_default,"
                " chat_limit_default, limit_reset, updated_by)"
                " SELECT account_cap_per_user, idle_expiry_days, monthly_credit_pool,"
                " fastest_schedule, research_limit_default, chat_limit_default, limit_reset,"
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
        research_limit_default=int(row.research_limit_default),
        chat_limit_default=int(row.chat_limit_default),
        limit_reset=str(row.limit_reset),
    )


async def accounts_used(session: Any, user_id: str) -> int:
    """How many accounts this user has added to the workspace."""
    value = await session.scalar(
        text("SELECT count(*) FROM accounts WHERE added_by = :u"), {"u": user_id}
    )
    return int(value or 0)


class AccountCapReached(Exception):
    """The user has added as many accounts as the client's cap allows."""

    def __init__(self, cap: int) -> None:
        super().__init__("account cap reached")
        self.cap = cap

    @property
    def message(self) -> str:
        """The design's words for it."""
        return f"You have used all {self.cap} accounts. Ask your administrator to raise the cap."


async def ensure_account_room(session: Any, user_id: str, adding: int = 1) -> None:
    """Refuse with ``AccountCapReached`` when ``adding`` more accounts would take the user past
    the client's cap. Locks the client's settings row, so two adds at once cannot both take the
    last place."""
    settings = await client_settings(session)
    await session.execute(text("SELECT 1 FROM client_settings FOR UPDATE"))
    if await accounts_used(session, user_id) + adding > settings.account_cap_per_user:
        raise AccountCapReached(settings.account_cap_per_user)


async def touch_activity(session: Any, user_id: str, now: Any) -> None:
    """Record that the user was active (idle expiry counts from here; an invited client becomes
    active). At most once a minute."""
    touched = (
        await session.execute(
            text(
                "UPDATE memberships SET last_active_at = :now WHERE user_id = :u"
                " AND access_state = 'active' AND (last_active_at IS NULL"
                " OR last_active_at < CAST(:now AS timestamptz) - interval '1 minute')"
                " RETURNING 1"
            ),
            {"u": user_id, "now": now},
        )
    ).first()
    if touched is not None:
        await session.execute(
            text("UPDATE client_settings SET status = 'active' WHERE status = 'invited'")
        )


DEFAULT_FIELDS = (
    "account_cap_per_user",
    "idle_expiry_days",
    "monthly_credit_pool",
    "fastest_schedule",
    "credit_cap_per_pull",
    "enabled_models",
    "default_research_model",
    "default_analysis_model",
    "max_file_bytes",
    "run_dollar_cap_usd",
    "repeat_call_limit",
    "stall_after_s",
    "research_limit_default",
    "chat_limit_default",
    "limit_reset",
    "skill_version",
)
CLIENT_FIELDS = (
    "account_cap_per_user",
    "idle_expiry_days",
    "monthly_credit_pool",
    "fastest_schedule",
    "mail_drive_allowed",
    "web_refresh_allowed",
    "research_limit_default",
    "chat_limit_default",
    "limit_reset",
)


async def start_invited(session: Any, actor: str) -> None:
    """A new client's settings, from the platform defaults, marked invited (inside its
    transaction)."""
    await session.execute(
        text(
            "INSERT INTO client_settings (account_cap_per_user, idle_expiry_days,"
            " monthly_credit_pool, fastest_schedule, research_limit_default,"
            " chat_limit_default, limit_reset, status, updated_by)"
            " SELECT account_cap_per_user, idle_expiry_days, monthly_credit_pool,"
            " fastest_schedule, research_limit_default, chat_limit_default, limit_reset,"
            " 'invited', :by FROM platform_defaults WHERE id = 1"
            " ON CONFLICT (tenant_id) DO NOTHING"
        ),
        {"by": actor},
    )


async def client_status(session: Any) -> str:
    """``invited`` until the client's first activity, then ``active``."""
    await client_settings(session)
    return str(await session.scalar(text("SELECT status FROM client_settings")))


async def update_client(session: Any, values: dict[str, Any], actor: str, now: Any) -> None:
    """Change the caller tenant's limits and features (only the named fields)."""
    await client_settings(session)
    fields = [f for f in CLIENT_FIELDS if f in values]
    if not fields:
        return
    sets = ", ".join(f"{f} = :{f}" for f in fields) + ", updated_by = :by, updated_at = :now"
    await session.execute(
        text(f"UPDATE client_settings SET {sets}"),  # nosec B608 -- fields from CLIENT_FIELDS; values bound.
        {**{f: values[f] for f in fields}, "by": actor, "now": now},
    )


async def set_allowed_models(session: Any, models: list[str], actor: str, now: Any) -> None:
    """The models the caller tenant's users may choose (owner decision 2026-10-14)."""
    await client_settings(session)
    await session.execute(
        text("UPDATE client_settings SET allowed_models = :m, updated_by = :by, updated_at = :now"),
        {"m": models, "by": actor, "now": now},
    )


async def defaults(session: Any) -> dict[str, Any]:
    """The platform defaults (one row)."""
    row = (
        await session.execute(
            text(
                "SELECT account_cap_per_user, idle_expiry_days, monthly_credit_pool,"
                " fastest_schedule, credit_cap_per_pull, enabled_models, default_research_model,"
                " default_analysis_model, research_limit_default, chat_limit_default,"
                " limit_reset, max_file_bytes, run_dollar_cap_usd, repeat_call_limit,"
                " stall_after_s, skill_version, updated_by, updated_at"
                " FROM platform_defaults WHERE id = 1"
            )
        )
    ).one()
    return dict(row._mapping)


async def update_defaults(session: Any, values: dict[str, Any], actor: str, now: Any) -> None:
    """Change the platform defaults (only the named fields; new clients start from them)."""
    fields = [f for f in DEFAULT_FIELDS if f in values]
    if not fields:
        return
    sets = ", ".join(f"{f} = :{f}" for f in fields) + ", updated_by = :by, updated_at = :now"
    sql = f"UPDATE platform_defaults SET {sets} WHERE id = 1"  # nosec B608 -- allow-listed fields
    await session.execute(text(sql), {**{f: values[f] for f in fields}, "by": actor, "now": now})
