"""Synthetic spend fakes: balances, a notifier, a cost table and a plan. No real provider, price
or tenant data; every value here is invented for the tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from spend.errors import NotConfigured
from spend.models import BalanceSnapshot, OpCost, PlanItem


class FakeBalances:
    """Scripted balance per provider code; a missing code raises ``NotConfigured``."""

    def __init__(self, statuses: dict[str, str], now: datetime) -> None:
        self.statuses, self.now = statuses, now
        self.asked: list[str] = []

    async def balance(self, provider_code: str) -> BalanceSnapshot:
        self.asked.append(provider_code)
        if provider_code not in self.statuses:
            raise NotConfigured(provider_code)
        status = self.statuses[provider_code]
        return BalanceSnapshot(
            provider_code=provider_code,
            status=status,  # type: ignore[arg-type]
            balance=None if status in ("unknown", "empty") else Decimal("1000"),
            unit="credit",
            source="synthetic balance",
            taken_at=self.now,
        )


class FakeNotifier:
    """Records prompts (identifiers only)."""

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str, int]] = []

    async def prompt(self, run_id: str, kind: str, version: int) -> None:
        self.prompts.append((run_id, kind, version))


def op(code: str, operation: str, low: int, expected: int, high: int, known: bool = True) -> OpCost:
    """A synthetic cost line in micro-dollars."""
    return OpCost(provider_code=code, operation=operation, low_micros=low, expected_micros=expected,
                  high_micros=high, known=known, cost_source="documented" if known else "unknown")


COSTS: dict[tuple[str, str], OpCost] = {
    ("p_search", "web_search"): op("p_search", "web_search", 1_000, 5_000, 5_000),
    ("p_signals", "job_openings"): op("p_signals", "job_openings", 0, 250_000, 2_500_000),
    ("p_people", "profile_enrichment"): op("p_people", "profile_enrichment", 0, 0, 0, known=False),
}

PLAN: Sequence[PlanItem] = (
    PlanItem(row_key="company_news", stage="news", priority="High", provider_code="p_search",
             operation="web_search", calls=8, mandatory=True),
    PlanItem(row_key="job_postings_12_months", stage="jobs", priority="High",
             provider_code="p_signals", operation="job_openings", calls=1, mandatory=True),
    PlanItem(row_key="full_profiles_senior_people", stage="people", priority="High",
             provider_code="p_people", operation="profile_enrichment", calls=40),
    PlanItem(row_key="research_papers", stage="news", priority="Optional", provider_code="p_search",
             operation="web_search", calls=4),
)
STAGES = ("setup", "news", "jobs", "people")
