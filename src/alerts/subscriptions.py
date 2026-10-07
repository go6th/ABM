"""Subscribe up (AL3, D-74): a new user, account or tenant receives the weekly digest only;
immediate rules, Slack and thresholds are switched on explicitly. Pure resolution of what a user
receives for one rule.

GUARANTEE(test_new_users_get_the_digest_only_and_subscribe_up)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

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
