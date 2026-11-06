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
sees the expected tokens and cost before confirming instead). Unset or ``on``, every cap applies;
the cloud profiles deploy ``on`` (fix prompt A, 2026-11-03, test_caps_default_on_in_cloud_profiles)
and a staging or production process started with the caps off logs ``research.caps_off`` naming
every cap it lifted (``app.container.warn_if_uncapped``; this module stays pure, O1).
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


def breadth_follows_source(env: Mapping[str, str] | None = None) -> bool:
    """True (the default) when what a research reads is bounded by the source, not by a count.

    Owner 2026-11-05, from the skill (phase1-pull.md: "page until the reported count is reached",
    "load every matched row"): a fixed count fits one size of company. 40 page reads or 2,000
    feed items cover a 1,000-person company and cut a 70,000-person one short on every run, so
    the same research ended Partial however often it was paid for. The breadth caps (pages,
    items, page reads, extractor calls, people, profiles, posts, reads per search) follow the
    source to its end. Time and spend stay with ``research_uncapped``: the slice count, provider
    timeouts and retries, the kept page length, the run's dollars, tokens, credits and call cap;
    repeated cursors and identical pages and the no-progress cut-off still bound a run.
    ``RESEARCH_BREADTH=capped`` restores the fixed counts."""
    e = os.environ if env is None else env
    return research_uncapped(e) or e.get("RESEARCH_BREADTH", "").strip().lower() != "capped"


def token_guard_forced(env: Mapping[str, str] | None = None) -> bool:
    """True when ``TOKEN_GUARD=on`` keeps the token-guard pause while the research caps are off
    (owner decision 2026-10-24: with the caps off a research is never paused for tokens unless
    this is set)."""
    e = os.environ if env is None else env
    return e.get("TOKEN_GUARD", "").strip().lower() == "on"


# every cap ``RESEARCH_CAPS=off`` lifts, by where it is enforced (named in research.caps_off)
LIFTED_CAPS: tuple[tuple[str, str], ...] = (
    ("run_dollar_cap", "connectors/spend_guard.check_cap: platform_defaults.run_dollar_cap_usd"),
    ("repeat_call_limit", "connectors/spend_guard.check_repeat: platform_defaults"),
    ("token_guard", "connectors/spend_guard.check_tokens: 3x the estimate (unless TOKEN_GUARD=on)"),
    ("agent_budgets", "agents/contracts.py: per-agent budgets from config/agents.yaml"),
    ("max_slices", "app/container._slices: max_slices from config/workflow_v1.yaml"),
    ("credit_budget", "runs/lifecycle.py: the run's credit budget and tool-call cap"),
    ("credit_pool", "replica/credits.py: the monthly credit pool"),
    ("user_research_limit", "replica/limits.py: researches per user"),
    ("plan_limits", "metering/limits.py: the plan's runs and accounts"),
    ("provider_limits", "connectors/settings.uncapped_config: timeouts, pages, polls, retries"),
    ("read_limits", "agents/production/provider.py, people/, jobs/: reads and people per run"),
)


def caps_lifted(env: Mapping[str, str] | None = None) -> list[str]:
    """The names of the caps that are off in ``env`` (empty when every cap applies)."""
    e = os.environ if env is None else env
    if not research_uncapped(e):
        return []
    forced = token_guard_forced(e)
    return [name for name, _ in LIFTED_CAPS if not (name == "token_guard" and forced)]
