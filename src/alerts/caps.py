"""Noise control (AL4, D-74) enforced in code. Pure: given what was already sent, decide whether
an immediate alert may go now, must be batched into the digest, or is suppressed (with a reason
that is recorded and counted).

* tier-one news: at most N per account per day (sourced cap, proposal value);
* per user: at most N an hour and M a day (proposals);
* above the tenant's daily digest threshold, immediate alerts are batched into the digest;
* digest-only rules are never real time.

GUARANTEE(test_caps_suppress_or_batch_with_a_reason)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Outcome = Literal["send", "batch", "suppress"]


@dataclass(frozen=True)
class Caps:
    """Configured caps."""

    news_per_account_day: int
    user_hour: int
    user_day: int
    digest_threshold: int


@dataclass(frozen=True)
class Sent:
    """Counts already delivered."""

    account_news_today: int
    user_last_hour: int
    user_today: int
    tenant_immediate_today: int


def decide(rule_id: str, tier: str, sent: Sent, caps: Caps) -> tuple[Outcome, str | None]:
    """The outcome and its reason."""
    if tier != "immediate":
        return "batch", "digest_rule"
    if rule_id == "tier_one_news" and sent.account_news_today >= caps.news_per_account_day:
        return "suppress", "account_news_cap"
    if sent.user_last_hour >= caps.user_hour:
        return "batch", "user_hourly_cap"
    if sent.user_today >= caps.user_day:
        return "batch", "user_daily_cap"
    if sent.tenant_immediate_today >= caps.digest_threshold:
        return "batch", "digest_threshold"
    return "send", None
