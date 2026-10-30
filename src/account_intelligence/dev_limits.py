"""Local stack switch that lifts the breadth and spend caps (``DEV_UNCAPPED=1``, dev only).

Production configuration keeps every cap. On the local stack a developer may want a run to read
everything a provider has: with ``APP_ENV=dev`` (the default) and ``DEV_UNCAPPED=1`` the credit
cap gate is skipped (the run keeps ``RUN_BUDGET_USD``), provider pagination, the job pipeline caps
and the technologies per lane are lifted to ``UNCAPPED``, and so is the slice ceiling: a run takes
as long as its research needs. Guards that only bound a loop (gap passes, retries, the slice length
itself) stay: they never limit what is researched.

``RESEARCH_CAPS=off`` lifts the same caps in any environment, staging included, and also every
spend and count limit a research meets: the run's credit budget and call cap, the platform dollar
cap and repeat-call limit, the monthly credit pool, the researches per user, the plan's runs and
accounts and the account cap per user (owner decision 2026-10-21, for the time being; the user
sees the expected tokens and cost before confirming instead). Unset, every cap applies.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from decimal import Decimal

UNCAPPED = 1_000_000
# a per-agent TOKEN budget with the caps off: one reading step uses more than a million tokens on a
# large account (staging 2026-10-30: 95 reading groups were dropped as budget_exceeded, so their
# facts were never read), so a count-sized UNCAPPED is not "no limit" for tokens
UNCAPPED_TOKENS = 1_000_000_000_000
# the run budget with the caps off: far above any run, inside account_runs.budget_usd numeric(12,4)
UNCAPPED_BUDGET_USD = Decimal("1000000")
# slice length on the uncapped local stack (production keeps the configured 240 s)
DEV_SLICE_SECONDS = 1200.0


def dev_uncapped(env: Mapping[str, str] | None = None) -> bool:
    """True on the local stack with ``DEV_UNCAPPED=1``; never in staging or production."""
    e = os.environ if env is None else env
    return e.get("APP_ENV", "dev") == "dev" and e.get("DEV_UNCAPPED") == "1"


def research_uncapped(env: Mapping[str, str] | None = None) -> bool:
    """True when the research caps are lifted: ``DEV_UNCAPPED`` on the local stack, or
    ``RESEARCH_CAPS=off`` in any environment."""
    e = os.environ if env is None else env
    return dev_uncapped(e) or e.get("RESEARCH_CAPS", "").strip().lower() == "off"


def token_guard_forced(env: Mapping[str, str] | None = None) -> bool:
    """True when ``TOKEN_GUARD=on`` keeps the token-guard pause while the research caps are off
    (owner decision 2026-10-24: with the caps off a research is never paused for tokens unless
    this is set)."""
    e = os.environ if env is None else env
    return e.get("TOKEN_GUARD", "").strip().lower() == "on"
