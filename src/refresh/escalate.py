"""Probe, then escalate (RF4, D-72): a pure decision recorded with its inputs.

Escalate to a full refresh when the delta score reaches the threshold OR the brief is older than
its maximum age, unless the account escalated too recently (minimum interval) or already reached
its cap for the period. The returned ``Decision`` is stored with the refresh run, so every
escalation (and every refusal) can be re-derived.

GUARANTEE(test_the_escalation_decision_is_pure_and_capped)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

Reason = Literal["delta_score", "brief_age", "below_threshold", "min_interval", "period_cap"]


@dataclass(frozen=True)
class Rules:
    """Configured limits (``config/refresh.yaml`` ``escalation``)."""

    threshold: int
    max_brief_age_days: int
    min_interval_days: int
    max_per_period: int


@dataclass(frozen=True)
class Decision:
    """The decision and everything it was computed from."""

    escalate: bool
    reason: Reason
    delta_score: int
    brief_age_days: int
    escalations_in_period: int
    last_escalation_at: datetime | None


def decide(
    *,
    now: datetime,
    delta_score: int,
    brief_built_at: datetime,
    last_escalation_at: datetime | None,
    escalations_in_period: int,
    rules: Rules,
) -> Decision:
    """The escalation decision."""
    age = (now - brief_built_at).days

    def out(escalate: bool, reason: Reason) -> Decision:
        return Decision(
            escalate, reason, delta_score, age, escalations_in_period, last_escalation_at
        )

    wanted: Reason | None = None
    if delta_score >= rules.threshold:
        wanted = "delta_score"
    elif age >= rules.max_brief_age_days:
        wanted = "brief_age"
    if wanted is None:
        return out(False, "below_threshold")
    if escalations_in_period >= rules.max_per_period:
        return out(False, "period_cap")
    if last_escalation_at is not None and now - last_escalation_at < timedelta(
        days=rules.min_interval_days
    ):
        return out(False, "min_interval")
    return out(True, wanted)
