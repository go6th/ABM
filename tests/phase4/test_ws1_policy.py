"""WS1 shelf-life policy and scheduling (SL1, RF8). Lying scenarios: a null cadence treated as
weekly, an override that shortens a shelf life, a value with no provenance, a duplicate tick, two
workers claiming one slot, a stampede after an outage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from account_intelligence.db.session import tenant_txn
from refresh import policy, schedule, slots
from tests.phase4.conftest import NOW, db, tenant_with_account

P = policy.load()


def test_due_dates_follow_the_policy_and_null_is_never_due() -> None:
    weekly = P.entry("job_board_by_lane")
    assert (weekly.cadence, weekly.days) == ("weekly", 7)
    assert policy.is_due(NOW, None, weekly.days)  # never checked: due
    assert not policy.is_due(NOW, NOW - timedelta(days=6), weekly.days)
    assert policy.is_due(NOW, NOW - timedelta(days=7), weekly.days)
    assert policy.due_at(NOW, weekly.days) == NOW + timedelta(days=7)
    nul = P.entry("vendor_customer_stories")
    assert nul.cadence is None and nul.days is None
    assert not policy.is_due(NOW, None, None) and policy.due_at(NOW, None) is None
    with pytest.raises(KeyError):
        P.entry("unknown_signal")  # no default policy
    assert all(e.provenance for e in P.entries.values())
    assert policy.effective_days(weekly, P, 14) == 14
    for bad in (3, 7 * 5):  # shorter, or beyond the bound
        with pytest.raises(policy.PolicyInvalid):
            policy.effective_days(weekly, P, bad)
    assert policy.effective_days(nul, P, 30) is None


def test_the_policy_refuses_values_without_provenance_or_bad_bounds() -> None:
    base = {
        "version": "x",
        "cadence_days": {"weekly": 7, "monthly": 30, "quarterly": 91},
        "tenant_override_bounds": {"min_factor": 1, "max_factor": 2},
        "signals": {"a": {"cadence": "weekly", "source": "s"}},
    }
    assert policy.parse(base).entry("a").days == 7
    for broken in (
        {**base, "signals": {"a": {"cadence": "weekly"}}},
        {**base, "signals": {"a": {"cadence": "daily", "source": "s"}}},
        {**base, "tenant_override_bounds": {"min_factor": 0.5, "max_factor": 2}},
        {**base, "cadence_days": {"weekly": 7}},
    ):
        with pytest.raises(policy.PolicyInvalid):
            policy.parse(broken)


def test_slot_periods_jitter_and_catch_up_are_bounded() -> None:
    at = datetime(2026, 3, 2, tzinfo=UTC)
    assert schedule.period_key("weekly", at) == "2026-W10"
    assert schedule.period_key("monthly", at) == "2026-03"
    assert schedule.period_key("quarterly", at) == "2026-Q1"
    with pytest.raises(ValueError):
        schedule.period_key("daily", at)
    a = uuid4()
    j = schedule.jitter_s(a, "probe", "2026-W10", 3600)
    assert 0 <= j < 3600 and j == schedule.jitter_s(a, "probe", "2026-W10", 3600)
    assert schedule.jitter_s(a, "probe", "2026-W10", 0) == 0


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.tuples(st.integers(0, 3), st.integers(0, 9), st.integers(0, 30), st.booleans()),
        max_size=80,
    ),
    st.integers(1, 20),
    st.integers(1, 6),
)
def test_catch_up_never_exceeds_its_bounds_or_duplicates(
    raw: list[tuple[int, int, int, bool]], per_tick: int, per_tenant: int
) -> None:
    tenants = [UUID(int=i + 1) for i in range(4)]
    missed = [
        schedule.MissedSlot(
            tenants[t], UUID(int=100 + acc), "probe", "2026-W10", NOW - timedelta(days=age), pa
        )
        for t, acc, age, pa in raw
    ]
    got = schedule.select_catch_up(missed, per_tick=per_tick, per_tenant=per_tenant)
    assert len(got) <= per_tick
    assert len({(m.account_id, m.kind, m.period) for m in got}) == len(got)
    for t in tenants:
        assert sum(m.tenant_id == t for m in got) <= per_tenant
    assert [m.due_at for m in got] == sorted(m.due_at for m in got)  # oldest due first


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_one_slot_one_execution() -> None:
    t, a = tenant_with_account("p4-slots")
    created = []
    for _ in range(5):  # a lost tick re-run, duplicate deliveries, a manual overlap
        async with tenant_txn(t) as s:
            created.append(
                await slots.ensure(s, account_id=a, kind="probe", period="2026-W10", due_at=NOW)
            )
    assert created == [True, False, False, False, False]

    async def worker(name: str) -> list[slots.Slot]:
        async with tenant_txn(t) as s:
            return await slots.claim(s, now=NOW + timedelta(hours=1), worker=name, limit=10)

    got = await asyncio.gather(*(worker(f"w{i}") for i in range(8)))
    assert sum(len(g) for g in got) == 1  # exactly one worker claimed it
    slot = next(x for g in got for x in g)
    async with tenant_txn(t) as s:
        assert await slots.finish(s, slot.id, status="done", outcome="no_change", now=NOW)
        assert not await slots.finish(s, slot.id, status="done", outcome="no_change", now=NOW)
        with pytest.raises(ValueError):
            await slots.finish(s, slot.id, status="weird", outcome="x", now=NOW)
    # after a 10-day outage the ticks ensure the missed periods once each, never twice
    for day in range(0, 21, 1):
        at = NOW + timedelta(days=day)
        for _ in range(2):
            async with tenant_txn(t) as s:
                await slots.ensure(
                    s,
                    account_id=a,
                    kind="probe",
                    period=schedule.period_key("weekly", at),
                    due_at=at,
                )
    rows = db("SELECT period, count(*) FROM refresh_slots WHERE tenant_id = %s GROUP BY period", t)
    assert all(n == 1 for _, n in rows) and len(rows) == 3  # ISO weeks 10, 11, 12
