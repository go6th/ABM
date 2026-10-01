#!/usr/bin/env python3
"""Spike 4 (T13): broker economics — run accounts through brokers behind a metering shim.

Measures per-run broker cost, cost per source group, coverage of expected attributes,
and produces the people-source decision (BitScale vs Sumble).

Providers:
  dry_run (default): synthetic accounts and deterministic broker pricing; no network.
  live             : real broker adapters are invoked through the same metering shim
                     (SPIKE4_BROKER_KEYS must be set; adapters land in Phase 1 T44-T46).

Usage:
    python spikes/spike4_economics/run.py --accounts 20
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
from dataclasses import dataclass, field
from typing import Any

COVERAGE_CAP = 0.80
COST_CAP_USD = 6.00  # per-run cap for standard depth (budget guard, Spec 5.6)

SOURCE_GROUPS = ("news", "jobs", "people", "tech")
EXPECTED_ATTRIBUTES = (
    "company.hq_location",
    "company.employee_count",
    "company.funding_stage",
    "exec.cto.name",
    "exec.cto.tenor_days",
    "tech.stack.primary_cloud",
    "jobs.open_roles_count",
    "news.recent_funding_event",
)

# Dry-run pricing per call in USD (list-price estimates; replaced by contract rates V7).
BROKER_PRICES: dict[str, dict[str, float]] = {
    "Parallel": {"news": 0.20, "jobs": 0.15, "people": 0.40, "tech": 0.25},
    "Firecrawl": {"news": 0.10, "jobs": 0.10, "people": 0.20, "tech": 0.10},
    "PredictLeads": {"news": 0.15, "jobs": 0.12, "people": 0.00, "tech": 0.10},
    "BitScale": {"news": 0.00, "jobs": 0.00, "people": 0.55, "tech": 0.30},
    "Sumble": {"news": 0.00, "jobs": 0.00, "people": 0.45, "tech": 0.35},
}
PEOPLE_BROKERS = ("BitScale", "Sumble")


@dataclass
class BrokerCall:
    """One metered broker call."""

    broker: str
    group: str
    cost_usd: float
    attributes_filled: list[str] = field(default_factory=list)


@dataclass
class AccountResult:
    """Metered result for one account."""

    domain: str
    calls: list[BrokerCall] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def covered_attributes(self) -> set[str]:
        covered: set[str] = set()
        for call in self.calls:
            covered.update(call.attributes_filled)
        return covered


@dataclass
class PeopleDecision:
    """BitScale vs Sumble comparison from the metered run."""

    bitscale_fill_rate: float
    sumble_fill_rate: float
    bitscale_cost_per_account: float
    sumble_cost_per_account: float
    decision: str
    rationale: str


def _synthesize_accounts(n: int, seed: int) -> list[str]:
    domains = [f"acct{i}.example.com" for i in range(1, n + 1)]
    return (
        domains[:n]
        if n <= len(domains)
        else domains + [f"extra{i}.example.com" for i in range(n - len(domains))]
    )


def _rng_for(seed: int) -> random.Random:
    return random.Random(seed)  # noqa: S311


def _run_account_dry(domain: str, rng: random.Random) -> AccountResult:
    """Deterministic synthetic run: each broker returns some attributes at list price."""
    result = AccountResult(domain=domain)
    for group in SOURCE_GROUPS:
        for broker, prices in BROKER_PRICES.items():
            price = prices.get(group, 0.0)
            if price <= 0.0 or rng.random() < 0.15:  # some sources unavailable per account
                continue
            filled = [
                a for a in EXPECTED_ATTRIBUTES if group in _group_of(a) and rng.random() < 0.45
            ]
            result.calls.append(BrokerCall(broker, group, price, filled))
    return result


def _group_of(attribute: str) -> str:
    if attribute.startswith(("exec.", "people.")):
        return "people"
    if attribute.startswith("jobs."):
        return "jobs"
    if attribute.startswith("tech."):
        return "tech"
    return "news"


def analyze(
    results: list[AccountResult],
) -> dict[str, Any]:
    """Compute cost per run, per source group, coverage and the people-source decision."""
    per_group: dict[str, list[float]] = {g: [] for g in SOURCE_GROUPS}
    for result in results:
        for group in SOURCE_GROUPS:
            per_group[group].append(sum(c.cost_usd for c in result.calls if c.group == group))

    costs = [r.total_cost for r in results]
    coverage = statistics.mean(
        len(r.covered_attributes) / len(EXPECTED_ATTRIBUTES) for r in results
    )

    def _people_stats(broker: str) -> tuple[float, float]:
        fills = [
            len(r.covered_attributes & {"exec.cto.name"}) / 1
            for r in results
            if any(c.broker == broker for c in r.calls)
        ]
        spend = [sum(c.cost_usd for c in r.calls if c.broker == broker) for r in results]
        fill_rate = statistics.mean(fills) if fills else 0.0
        cost_per = statistics.mean(spend) if spend else 0.0
        return fill_rate, cost_per

    bs_fill, bs_cost = _people_stats("BitScale")
    sb_fill, sb_cost = _people_stats("Sumble")
    if bs_fill > sb_fill and abs(bs_cost - sb_cost) < 0.15:
        decision = PeopleDecision(
            bs_fill,
            sb_fill,
            bs_cost,
            sb_cost,
            "BitScale",
            "Higher fill rate at comparable per-account cost",
        )
    elif sb_fill >= bs_fill and sb_cost <= bs_cost + 0.15:
        decision = PeopleDecision(
            bs_fill,
            sb_fill,
            bs_cost,
            sb_cost,
            "Sumble",
            "Equal-or-better fill at lower cost",
        )
    else:
        decision = PeopleDecision(
            bs_fill,
            sb_fill,
            bs_cost,
            sb_cost,
            "BitScale + Sumble split",
            "Neither source alone meets coverage; split by attribute",
        )

    return {
        "accounts": len(results),
        "mean_cost_per_run_usd": round(statistics.mean(costs), 3),
        "max_cost_per_run_usd": round(max(costs), 3),
        "cost_per_source_group_usd": {
            g: round(statistics.mean(v), 3) for g, v in per_group.items()
        },
        "coverage_of_expected_attributes": round(coverage, 3),
        "within_cost_cap": max(costs) <= COST_CAP_USD,
        "coverage_meets_target": coverage >= COVERAGE_CAP,
        "people_source_decision": {
            "bitscale_fill_rate": round(decision.bitscale_fill_rate, 3),
            "sumble_fill_rate": round(decision.sumble_fill_rate, 3),
            "bitscale_cost_per_account_usd": round(decision.bitscale_cost_per_account, 3),
            "sumble_cost_per_account_usd": round(decision.sumble_cost_per_account, 3),
            "decision": decision.decision,
            "rationale": decision.rationale,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounts", type=int, default=20)
    parser.add_argument("--provider", default="dry_run", choices=["dry_run", "live"])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json-out", default="")
    options = parser.parse_args()

    if options.provider == "live":
        if not os.environ.get("SPIKE4_BROKER_KEYS"):
            print("SPIKE4_BROKER_KEYS required for --provider live (set in .env)")
            return 2
        print("live broker adapters land in Phase 1 (T44-T46); aborting before any spend")
        return 2

    domains = _synthesize_accounts(options.accounts, options.seed)
    results = [_run_account_dry(d, _rng_for(options.seed + i)) for i, d in enumerate(domains)]
    report = analyze(results)

    print(f"metered dry-run over {report['accounts']} accounts x 5 brokers (shim)")
    print(f"mean cost per run  = ${report['mean_cost_per_run_usd']}")
    print(f"max  cost per run  = ${report['max_cost_per_run_usd']} (cap ${COST_CAP_USD:.2f})")
    for group, cost in report["cost_per_source_group_usd"].items():
        print(f"  {group:>6}: ${cost} per run")
    coverage = report["coverage_of_expected_attributes"]
    print(f"coverage of expected attributes = {coverage} (target {COVERAGE_CAP})")
    people = report["people_source_decision"]
    bs_fill, sb_fill = people["bitscale_fill_rate"], people["sumble_fill_rate"]
    bs_cost = people["bitscale_cost_per_account_usd"]
    sb_cost = people["sumble_cost_per_account_usd"]
    print(
        f"people decision: {people['decision']} "
        f"(BitScale fill {bs_fill} @ ${bs_cost}, Sumble fill {sb_fill} @ ${sb_cost})"
    )
    print(
        "SPIKE4 RESULT:",
        "PASS" if report["within_cost_cap"] and report["coverage_meets_target"] else "FAIL",
    )
    if options.json_out:
        with open(options.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
