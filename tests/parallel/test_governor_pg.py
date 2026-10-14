"""The Postgres governor (migration 0017) on a real database: the SQL rule equals the Python rule,
the limit holds under 100 concurrent workers across 50 runs and two tenants (a monitor polls the
active count continuously), a dead holder's lease is reclaimed, AIMD moves the limit, and no tenant
can read or release another tenant's leases."""

from __future__ import annotations

import asyncio
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import text

from account_intelligence.connectors.base import CallContext
from account_intelligence.db.session import tenant_txn
from parallel.config import load_parallel_config
from parallel.governor import Lease, PostgresGovernor, Shares, Waiter, choose
from tests.conftest import ADMIN_DSN, _make_tenant

pytestmark = [pytest.mark.requires_postgres]
SH = Shares(50, 50, 30)


class RealClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def admin(sql: str, *params: object) -> list[tuple[object, ...]]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def ctx(tenant: UUID, run: UUID) -> CallContext:
    return CallContext(tenant, run, uuid4(), "synthetic.example.com")


def settings_for(ceiling: int) -> object:
    return replace(
        load_parallel_config().providers,
        default_ceiling=ceiling,
        lease_poll_s=0.01,
        lease_jitter_s=0.01,
        lease_wait_s=30.0,
        heartbeat_s=0.5,
        lease_ttl_s=5.0,
    )


@pytest.fixture(scope="module")
def tenants() -> tuple[UUID, UUID, UUID]:
    return (_make_tenant("gov-a"), _make_tenant("gov-b"), _make_tenant("gov-c"))


@settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(st.data())
def test_postgres_and_memory_governors_agree(
    tenants: tuple[UUID, UUID, UUID], data: st.DataObject
) -> None:
    provider = f"p_parity_{uuid4().hex[:8]}"
    now = datetime(2026, 5, 1, 12, tzinfo=UTC)
    limit = data.draw(st.integers(1, 4))
    runs = [uuid4() for _ in range(3)]
    admin(
        "INSERT INTO provider_limits (provider_code, current_limit, ceiling) VALUES (%s, %s, %s)",
        provider,
        limit,
        9,
    )
    leases: list[Lease] = []
    for i in range(data.draw(st.integers(0, limit + 1))):
        t, r = data.draw(st.sampled_from(tenants)), data.draw(st.sampled_from(runs))
        admin(
            "INSERT INTO provider_leases (tenant_id, run_id, provider_code, holder, acquired_at,"
            " expires_at, heartbeat_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            t,
            r,
            provider,
            f"l{i}",
            now,
            now + timedelta(minutes=5),
            now,
        )
        leases.append(Lease(f"l{i}", str(t), str(r), f"l{i}", now + timedelta(minutes=5)))
    waiters: list[Waiter] = []
    for i in range(data.draw(st.integers(0, 4))):
        t, r = data.draw(st.sampled_from(tenants)), data.draw(st.sampled_from(runs))
        since = now - timedelta(seconds=data.draw(st.integers(0, 200)))
        prio = data.draw(st.integers(0, 2))
        admin(
            "INSERT INTO provider_waiters (tenant_id, run_id, provider_code, holder, priority,"
            " since, seen_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            t,
            r,
            provider,
            f"w{i}",
            prio,
            since,
            now,
        )
        waiters.append(Waiter(str(t), str(r), f"w{i}", prio, since, now))
    me_t, me_r = data.draw(st.sampled_from(tenants)), data.draw(st.sampled_from(runs))
    me_prio = data.draw(st.integers(0, 2))
    expected = choose(
        limit, leases, [*waiters, Waiter(str(me_t), str(me_r), "me", me_prio, now, now)], SH, now
    )

    from tests.conftest import APP_DSN

    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.scope', 'system', true)",
            (str(me_t),),
        )
        got = c.execute(
            "SELECT governor_acquire(%s, %s, 'me', %s, %s, 60, 60, 50, 50, 30, 9, 1)",
            (provider, me_r, me_prio, now),
        ).fetchone()[0]
    assert (got is not None) == (expected == "me")


@pytest.mark.asyncio
async def test_limit_holds_under_100_workers_and_50_runs(tenants: tuple[UUID, UUID, UUID]) -> None:
    provider = f"p_load_{uuid4().hex[:8]}"
    limit = 3
    gov = PostgresGovernor(
        tenant_txn,
        settings_for(limit),
        SH,
        RealClock(),
        random.Random(7),  # type: ignore[arg-type]
    )
    runs = [(tenants[i % 2], uuid4()) for i in range(50)]
    peak = 0
    stop = asyncio.Event()

    async def monitor() -> None:
        nonlocal peak
        while not stop.is_set():
            async with tenant_txn(tenants[0]) as s:
                active = (
                    await s.execute(
                        text("SELECT governor_active(:p, :now)"),
                        {"p": provider, "now": datetime.now(UTC)},
                    )
                ).scalar_one()
            peak = max(peak, int(active))
            assert active <= limit
            await asyncio.sleep(0.005)

    in_flight = 0
    seen_max = 0

    async def worker(i: int) -> None:
        nonlocal in_flight, seen_max
        tenant, run = runs[i % len(runs)]
        async with gov.slot(ctx(tenant, run), provider, i % 2):
            in_flight += 1
            seen_max = max(seen_max, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1

    watch = asyncio.create_task(monitor())
    await asyncio.gather(*(worker(i) for i in range(100)))
    stop.set()
    await watch
    assert seen_max <= limit and peak <= limit
    granted = admin("SELECT count(*) FROM provider_leases WHERE provider_code = %s", provider)
    assert granted == [(100,)]
    open_ = admin(
        "SELECT count(*) FROM provider_leases WHERE provider_code = %s AND released_at IS NULL",
        provider,
    )
    assert open_ == [(0,)]


@pytest.mark.asyncio
async def test_a_killed_workers_lease_is_reclaimed_after_its_ttl(
    tenants: tuple[UUID, UUID, UUID],
) -> None:
    provider = f"p_dead_{uuid4().hex[:8]}"
    now = datetime.now(UTC)
    async with tenant_txn(tenants[0]) as s:
        dead = (
            await s.execute(
                text("SELECT governor_acquire(:p, :r, 'dead', 0, :now, 1, 60, 100, 100, 30, 1, 1)"),
                {"p": provider, "r": uuid4(), "now": now},
            )
        ).scalar_one()
    assert dead is not None
    async with tenant_txn(tenants[1]) as s:
        blocked = (
            await s.execute(
                text("SELECT governor_acquire(:p, :r, 'next', 0, :now, 1, 60, 100, 100, 30, 1, 1)"),
                {"p": provider, "r": uuid4(), "now": now},
            )
        ).scalar_one()
        later = (
            await s.execute(
                text("SELECT governor_acquire(:p, :r, 'next', 0, :now, 1, 60, 100, 100, 30, 1, 1)"),
                {"p": provider, "r": uuid4(), "now": now + timedelta(seconds=2)},
            )
        ).scalar_one()
    assert blocked is None and later is not None
    reason = admin("SELECT release_reason FROM provider_leases WHERE id = %s", dead)
    assert reason == [("expired",)]  # kept, marked, never deleted


@pytest.mark.asyncio
async def test_aimd_in_sql_and_limit_events(tenants: tuple[UUID, UUID, UUID]) -> None:
    provider = f"p_aimd_{uuid4().hex[:8]}"
    t0 = datetime(2026, 5, 1, tzinfo=UTC)
    async with tenant_txn(tenants[0]) as s:
        await s.execute(
            text("SELECT governor_configure(:p, 8, 1, :now)"), {"p": provider, "now": t0}
        )
    admin("UPDATE provider_limits SET current_limit = 8 WHERE provider_code = %s", provider)

    async def sig(kind: str, at: datetime) -> None:
        async with tenant_txn(tenants[0]) as s:
            await s.execute(
                text("SELECT governor_signal(:p, :k, :now, 60)"),
                {"p": provider, "k": kind, "now": at},
            )

    await sig("rate_limited", t0)
    await sig("rate_limited", t0 + timedelta(seconds=5))
    await sig("clean", t0 + timedelta(seconds=61))  # 56 s after the last rate-limit signal: no
    await sig("clean", t0 + timedelta(seconds=66))  # a full clean window: plus one
    await sig("clean", t0 + timedelta(seconds=70))  # one change per window
    assert admin(
        "SELECT current_limit FROM provider_limits WHERE provider_code = %s", provider
    ) == [(5,)]
    events = admin(
        "SELECT event, old_limit, new_limit FROM provider_limit_events WHERE provider_code = %s"
        " ORDER BY id",
        provider,
    )
    assert events == [("configured", None, 1), ("decrease", 8, 4), ("increase", 4, 5)]


@pytest.mark.asyncio
async def test_no_tenant_reads_or_releases_another_tenants_lease(
    tenants: tuple[UUID, UUID, UUID],
) -> None:
    provider = f"p_iso_{uuid4().hex[:8]}"
    now = datetime.now(UTC)
    async with tenant_txn(tenants[0]) as s:
        lease = (
            await s.execute(
                text("SELECT governor_acquire(:p, :r, 'a', 0, :now, 60, 60, 100, 100, 30, 5, 1)"),
                {"p": provider, "r": uuid4(), "now": now},
            )
        ).scalar_one()
    async with tenant_txn(tenants[1]) as s:
        seen = (
            await s.execute(
                text("SELECT count(*) FROM provider_leases WHERE provider_code = :p"),
                {"p": provider},
            )
        ).scalar_one()
        await s.execute(text("SELECT governor_release(:l, :now)"), {"l": lease, "now": now})
        ok = (
            await s.execute(
                text("SELECT governor_heartbeat(:l, :now, 60)"), {"l": lease, "now": now}
            )
        ).scalar_one()
        with pytest.raises(Exception, match="permission denied"):
            await s.execute(text("UPDATE provider_leases SET released_at = now()"))
    assert seen == 0 and ok is False
    assert admin("SELECT released_at FROM provider_leases WHERE id = %s", lease) == [(None,)]


def test_insert_only_unit_tables_refuse_update_and_delete(tenants: tuple[UUID, UUID, UUID]) -> None:
    from tests.conftest import APP_DSN

    with psycopg.connect(APP_DSN, autocommit=True) as c:
        for table in (
            "work_units",
            "work_unit_events",
            "spend_limit_events",
            "notification_outbox",
            "prepulled_intents",
            "prepulled_intent_events",
        ):
            for sql in (f"UPDATE {table} SET tenant_id = tenant_id", f"DELETE FROM {table}"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute(sql)
