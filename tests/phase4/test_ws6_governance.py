"""WS6 monitoring cost governance (CG1 to CG3). Lying scenarios: 100 parallel probes at the limit,
an unpriced call counted as free, the 80% alert repeated, priority A throttled first, a tenant view
with units or dollars, a ratio stated on two runs."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from account_intelligence.db.session import tenant_txn
from governance import account_cost, monitoring_budget, throttle
from tests.phase4.conftest import NOW, db, tenant_with_account

LADDER = monitoring_budget.config()["ladder"]


def accts(n: int, priority_a: set[int]) -> list[throttle.Account]:
    return [
        throttle.Account(f"a{i:02d}", score=i * 10, priority_a=i in priority_a) for i in range(n)
    ]


def test_the_throttle_ladder_is_deterministic_and_spares_priority_a() -> None:
    a = accts(6, {0})  # a00 has the lowest score but is priority A
    assert set(throttle.plan(79.9, a, LADDER).values()) == {"normal"}
    at80 = throttle.plan(80, a, LADDER)
    assert at80 == {
        "a00": "normal",
        "a01": "probe_only",
        "a02": "probe_only",
        "a03": "normal",
        "a04": "normal",
        "a05": "normal",
    }  # lowest-scoring half of the others
    at90 = throttle.plan(90, a, LADDER)
    assert at90["a01"] == "probe_only" and at90["a05"] == "lengthened" and at90["a00"] == "normal"
    at95 = throttle.plan(95, a, LADDER)
    assert at95["a00"] == "normal" and {at95[f"a0{i}"] for i in range(1, 6)} == {"paused"}
    assert set(throttle.plan(100, a, LADDER).values()) == {"stopped"}


@settings(max_examples=80, deadline=None)
@given(st.floats(0, 100), st.integers(0, 12), st.sets(st.integers(0, 11)))
def test_the_ladder_is_a_pure_function(pct: float, n: int, pa: set[int]) -> None:
    a = accts(n, pa)
    p1 = throttle.plan(pct, a, LADDER)
    assert p1 == throttle.plan(pct, list(reversed(a)), LADDER)
    if pct < 100:
        assert all(p1[x.account_id] == "normal" for x in a if x.priority_a)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_monitoring_budget_is_atomic_and_alerts_once() -> None:
    t, _ = tenant_with_account("p4-budget")
    async with tenant_txn(t) as s:
        await monitoring_budget.set_allowance(
            s, units=100, set_by="op", reason_code="pilot", now=NOW
        )

    async def one() -> str:
        try:
            async with tenant_txn(t) as s:
                await monitoring_budget.reserve(s, units=1, plan="pilot", now=NOW)
            return "ok"
        except monitoring_budget.BudgetExhausted:
            return "denied"

    results = await asyncio.gather(*(one() for _ in range(130)))
    assert results.count("ok") == 100 and results.count("denied") == 30
    assert db("SELECT used FROM monitoring_usage WHERE tenant_id = %s", t) == [(100,)]
    notices = db("SELECT template, params_json FROM notification_outbox WHERE tenant_id = %s", t)
    assert len(notices) == 1 and notices[0][0] == "monitoring_budget_near_limit"
    assert "credit" not in str(notices[0][1]).lower() and "$" not in str(notices[0][1])
    steps = {s for (s,) in db("SELECT step FROM throttle_events WHERE tenant_id = %s", t)}
    assert steps == {
        "alert_80",
        "probe_only_lowest_scores",
        "lengthen_cadence",
        "pause_non_priority",
        "hard_stop",
    }
    async with tenant_txn(t) as s:
        view = await monitoring_budget.tenant_view(s, "pilot", NOW)
    assert view == {"period": "2026-03", "percent": 100}  # percent only
    # a new month resets without rollover, and an unpriced call counts at the upper bound
    april = NOW + timedelta(days=31)
    async with tenant_txn(t) as s:
        r = await monitoring_budget.reserve(s, units=None, plan="pilot", now=april)
        assert (r.units, r.used) == (monitoring_budget.config()["unknown_cost_units"], r.units)
        with pytest.raises(ValueError):
            await monitoring_budget.reserve(s, units=-1, plan="pilot", now=april)
    assert db("SELECT count(*) FROM notification_outbox WHERE tenant_id = %s", t) == [(1,)]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_plan_default_applies_without_an_operator_allowance() -> None:
    t, _ = tenant_with_account("p4-budget-default")
    async with tenant_txn(t) as s:
        assert (
            await monitoring_budget.allowance(s, "pilot")
            == monitoring_budget.config()["plans"]["pilot"]
        )
        r = await monitoring_budget.reserve(s, units=10, plan="pilot", now=NOW)
    assert r.used_percent == 0 and not r.crossed_80


def test_cost_reports_use_measured_rows_and_say_not_enough_data() -> None:
    small = account_cost.ratio_of([300_000] * 3, [5_600_000] * 3, NOW)
    assert small.ratio is None and small.statement.startswith("not enough data")
    full = account_cost.ratio_of([300_000] * 6 + [1_000_000] * 4, [5_000_000] * 10, NOW)
    assert full.refresh_mean_micros == 580_000 and full.full_mean_micros == 5_000_000
    assert full.ratio == 0.116 and "base 10 refreshes, 10 full runs" in full.statement
    zero = account_cost.ratio_of([1] * 10, [0] * 10, NOW)
    assert zero.ratio is None


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_per_account_cost_and_the_ratio_read_the_database() -> None:
    from signals import intake, registry

    t, a = tenant_with_account("p4-cost")
    async with tenant_txn(t) as s:
        for kind in ("news_events", "news_events", "facility_expansions"):
            await intake.run_monitor(
                s,
                registry.UnconfiguredMonitor(kind),
                account_id=a,  # type: ignore[arg-type]
                domain="x.example",
                since=None,
                refresh_run_id=None,
                units=4,
                throttled=False,
                now=NOW,
            )
        rows = await account_cost.per_account(s)
        ratio = await account_cost.refresh_ratio(s, NOW)
    assert [(r["kind"], r["units"], r["runs"]) for r in rows] == [
        ("facility_expansions", 0, 1),
        ("news_events", 0, 2),
    ]  # not_configured costs nothing
    assert ratio.ratio is None and ratio.refresh_runs == 0
