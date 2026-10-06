"""The governor's pure rules and the in-memory shell (P4, P5, P6): limits, fairness, AIMD, the
bounded wait, heartbeat and reclaim of a dead holder's lease."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from account_intelligence.connectors.base import CallContext
from account_intelligence.connectors.governance import SlotUnavailable
from parallel.config import load_parallel_config
from parallel.governor import (
    InMemoryGovernor,
    Lease,
    LimitState,
    Shares,
    Waiter,
    aimd,
    choose,
    share,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
SH = Shares(50, 50, 30)


class Clock:
    """Virtual time; sleep advances it."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> datetime:
        return T0 + timedelta(seconds=self.t)

    async def sleep(self, seconds: float) -> None:
        self.t += seconds
        await asyncio.sleep(0)


def ctx(tenant: str | None = None, run: str | None = None) -> CallContext:
    from uuid import UUID

    return CallContext(
        tenant_id=UUID(tenant) if tenant else uuid4(),
        run_id=UUID(run) if run else uuid4(),
        account_id=uuid4(),
        account_domain="synthetic.example.com",
    )


@st.composite
def queues(draw: st.DrawFn) -> tuple[int, list[Lease], list[Waiter]]:
    limit = draw(st.integers(1, 6))
    tenants = [f"t{i}" for i in range(3)]
    leases = [
        Lease(
            f"l{i}",
            draw(st.sampled_from(tenants)),
            f"r{draw(st.integers(0, 3))}",
            f"h{i}",
            T0 + timedelta(seconds=60),
        )
        for i in range(draw(st.integers(0, limit + 2)))
    ]
    waiters = [
        Waiter(
            draw(st.sampled_from(tenants)),
            f"r{draw(st.integers(0, 3))}",
            f"w{i}",
            draw(st.integers(0, 2)),
            T0 - timedelta(seconds=draw(st.integers(0, 300))),
            T0,
        )
        for i in range(draw(st.integers(0, 6)))
    ]
    return limit, leases, waiters


@given(queues())
def test_governor_never_grants_at_or_above_the_limit(q: tuple[int, list, list]) -> None:
    limit, leases, waiters = q
    chosen = choose(limit, leases, waiters, SH, T0)
    if len(leases) >= limit:
        assert chosen is None
    if chosen is not None:
        w = next(x for x in waiters if x.holder == chosen)
        assert sum(1 for x in leases if x.run == w.run) < share(limit, SH.run_pct)
        assert sum(1 for x in leases if x.tenant == w.tenant) < share(limit, SH.tenant_pct)
    assert choose(limit, list(reversed(leases)), list(reversed(waiters)), SH, T0) == chosen


@given(queues())
def test_governor_fairness_bounds_a_floods_advantage(q: tuple[int, list, list]) -> None:
    """The next slot goes to a waiter whose tenant holds the fewest slots among eligible waiters:
    a tenant with many waiters and slots can never take a slot another eligible tenant with fewer
    slots is waiting for."""
    limit, leases, waiters = q
    chosen = choose(limit, leases, waiters, SH, T0)
    if chosen is None:
        return
    held = {t: sum(1 for x in leases if x.tenant == t) for t in {w.tenant for w in waiters}}
    winner = next(w for w in waiters if w.holder == chosen)
    for w in waiters:
        eligible = sum(1 for x in leases if x.run == w.run) < share(limit, SH.run_pct) and held[
            w.tenant
        ] < share(limit, SH.tenant_pct)
        if eligible:
            assert held[winner.tenant] <= held[w.tenant]


def test_aging_lets_a_low_priority_waiter_overtake() -> None:
    old = Waiter("t1", "r1", "old", 0, T0 - timedelta(seconds=95), T0)
    vip = Waiter("t2", "r2", "vip", 2, T0, T0)
    assert choose(1, [], [old, vip], SH, T0) == "old"  # 0 + 95 // 30 = 3 > 2
    assert choose(1, [], [old, vip], SH, T0 - timedelta(seconds=40)) == "vip"


def test_aimd_halves_on_rate_limit_and_grows_by_one_per_clean_window() -> None:
    s = LimitState(8, 10, 1)
    s, e = aimd(s, "rate_limited", T0, 60)
    assert (s.limit, e) == (4, "decrease")
    s, e = aimd(s, "rate_limited", T0 + timedelta(seconds=10), 60)
    assert (s.limit, e) == (4, None)  # at most one change per window
    s, e = aimd(s, "clean", T0 + timedelta(seconds=50), 60)
    assert e is None  # not clean for a full window since the last rate-limit signal
    s, e = aimd(s, "clean", T0 + timedelta(seconds=71), 60)
    assert (s.limit, e) == (5, "increase")
    s, e = aimd(s, "clean", T0 + timedelta(seconds=80), 60)
    assert e is None
    top = LimitState(10, 10, 1, T0, None)
    assert aimd(top, "clean", T0 + timedelta(hours=1), 60)[1] is None  # never above the ceiling
    one = LimitState(1, 10, 1)
    assert aimd(one, "rate_limited", T0, 60)[0].limit == 1  # never below the floor
    fresh, e = aimd(LimitState(1, 3, 1), "clean", T0, 60)
    assert e is None and fresh.last_change_at == T0  # the first clean window starts now
    assert aimd(fresh, "clean", T0 + timedelta(seconds=60), 60)[0].limit == 2


def _gov(clock: Clock, ceiling: int = 2) -> InMemoryGovernor:
    from dataclasses import replace

    settings = replace(load_parallel_config().providers, default_ceiling=ceiling)
    return InMemoryGovernor(settings, SH, clock)


def test_slot_waits_a_bounded_time_then_raises_slot_unavailable() -> None:
    clock = Clock()
    gov = _gov(clock, ceiling=1)

    async def scenario() -> float:
        async with gov.slot(ctx(), "p_x", 0):
            with pytest.raises(SlotUnavailable) as info:
                async with gov.slot(ctx(), "p_x", 0):
                    pass  # pragma: no cover
            return info.value.waited_s

    waited = asyncio.run(scenario())
    assert waited >= gov.settings.lease_wait_s
    assert gov.active("p_x") == 0  # released in finally


def test_governor_reclaims_a_dead_holders_lease() -> None:
    clock = Clock()
    gov = _gov(clock, ceiling=1)
    assert gov.acquire_now("p_x", "t1", "r1", "dead", 0) is not None
    assert gov.acquire_now("p_x", "t2", "r2", "next", 0) is None
    clock.t += gov.settings.lease_ttl_s + 1  # the holder died and never heartbeat
    assert gov.acquire_now("p_x", "t2", "r2", "next", 0) is not None


def test_heartbeat_keeps_a_long_call_and_signals_move_the_limit() -> None:
    clock = Clock()
    gov = _gov(clock, ceiling=3)

    async def scenario() -> None:
        c = ctx()
        async with gov.slot(c, "p_x", 0):
            for _ in range(10):  # longer than the TTL: the heartbeat renews it
                await clock.sleep(gov.settings.heartbeat_s)
            assert gov.active("p_x") == 1
        await gov.signal(c, "p_x", "clean")
        clock.t += gov.settings.aimd_window_s
        await gov.signal(c, "p_x", "clean")
        assert gov.state("p_x").limit.limit == 2
        await gov.signal(c, "p_x", "rate_limited")
        assert gov.state("p_x").limit.limit == 2  # one change per window

    asyncio.run(scenario())
    assert [e[1] for e in gov.events] == ["increase"]
    assert gov.state("p_x").grants_at_limit == 0
