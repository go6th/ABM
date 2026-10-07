"""Subscribe up (AL3, D-74): a new user, account or tenant receives the weekly digest only;
immediate rules, Slack and thresholds are switched on explicitly. Pure resolution of what a user
receives for one rule.

Storage: insert-only versions per user (newest wins); absent means the defaults. A bulk
"subscribe to all" is an explicit, audited action (``bulk`` true). The one writer of
``subscriptions``.

GUARANTEE(test_new_users_get_the_digest_only_and_subscribe_up)
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text

from alerts.rules import Rule


@dataclass(frozen=True)
class Subscription:
    """A user's settings (absent rows mean the defaults)."""

    digest: bool = True
    immediate_rules: frozenset[str] = frozenset()
    slack: bool = False
    threshold: int = 0
    muted_accounts: frozenset[str] = frozenset()
    snoozed_until: Mapping[str, float] | None = None


Channel = Literal["in_app", "email", "slack", "digest"]


def channels(
    sub: Subscription, rule: Rule, *, account: str, score: int, now_ts: float
) -> tuple[list[Channel], str | None]:
    """The channels for one event and the suppression reason when there are none."""
    if account in sub.muted_accounts:
        return [], "muted"
    snooze = (sub.snoozed_until or {}).get(account)
    if snooze is not None and now_ts < snooze:
        return [], "snoozed"
    if score < sub.threshold:
        return [], "below_threshold"
    if rule.tier == "immediate" and rule.id in sub.immediate_rules:
        out: list[Channel] = ["in_app", "email"]
        if sub.slack:
            out.append("slack")
        return out, None
    if sub.digest:
        return ["in_app", "digest"], None
    return [], "not_subscribed"


def _to_json(sub: Subscription) -> dict[str, Any]:
    return {
        "digest": sub.digest,
        "immediate_rules": sorted(sub.immediate_rules),
        "slack": sub.slack,
        "threshold": sub.threshold,
        "muted_accounts": sorted(sub.muted_accounts),
        "snoozed_until": dict(sub.snoozed_until or {}),
    }


def _from_json(d: Mapping[str, Any]) -> Subscription:
    return Subscription(
        bool(d.get("digest", True)),
        frozenset(d.get("immediate_rules") or ()),
        bool(d.get("slack", False)),
        int(d.get("threshold", 0)),
        frozenset(d.get("muted_accounts") or ()),
        dict(d.get("snoozed_until") or {}),
    )


async def current(s: Any, user_id: str) -> Subscription:
    """A user's settings (the defaults when none were saved)."""
    row = (
        await s.execute(
            text(
                "SELECT settings FROM subscriptions WHERE user_id = :u ORDER BY version DESC"
                " LIMIT 1"
            ),
            {"u": user_id},
        )
    ).first()
    return Subscription() if row is None else _from_json(row.settings)


async def save(
    s: Any, user_id: str, sub: Subscription, *, set_by: str, bulk: bool, now: datetime
) -> int:
    """A new settings version; returns it."""
    version = int(
        (
            await s.execute(
                text("SELECT coalesce(max(version), 0) + 1 FROM subscriptions WHERE user_id = :u"),
                {"u": user_id},
            )
        ).scalar_one()
    )
    await s.execute(
        text(
            "INSERT INTO subscriptions (user_id, version, settings, set_by, bulk, created_at)"
            " VALUES (:u, :v, CAST(:st AS jsonb), :by, :b, :now)"
        ),
        {
            "u": user_id,
            "v": version,
            "st": json.dumps(_to_json(sub), sort_keys=True),
            "by": set_by,
            "b": bulk,
            "now": now,
        },
    )
    return version
