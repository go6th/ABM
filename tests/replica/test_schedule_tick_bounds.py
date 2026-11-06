"""The scheduled-refresh tick is bounded (fix prompt A, task 4): with caps off a large batch of
due accounts used to start together. 25 due accounts across 4 tenants: one tick starts at most
``platform_defaults.schedule_tick_max_starts`` (10) and at most ``schedule_tick_max_per_tenant``
(3) per tenant; the accounts left over keep their ``next_refresh_at`` and start on later ticks.
SYNTHETIC data only."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.replica import schedules as SC

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

PER_TENANT = {0: 7, 1: 6, 2: 6, 3: 6}  # 25 accounts


@pytest.fixture(autouse=True)
def _automatic_starts_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests exercise the schedule tick itself (off unless the switch is on)."""
    monkeypatch.setenv("RESEARCH_AUTOMATIC_STARTS", "on")


def _sql(sql: str, *args: Any) -> list[Any]:
    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, args)
        return cur.fetchall() if cur.description else []


class Recorder:
    """Records the jobs a tick starts (instead of running them)."""

    def __init__(self) -> None:
        self.started: list[tuple[UUID, UUID]] = []

    async def __call__(self, tenant: UUID, job: UUID) -> None:
        self.started.append((tenant, job))


@pytest.fixture
def due_world() -> Any:
    now = datetime.now(UTC)
    tenants: list[UUID] = []
    accounts: dict[UUID, UUID] = {}
    for i, n in PER_TENANT.items():
        tenant = uuid4()
        tenants.append(tenant)
        _sql("INSERT INTO tenants (id, name) VALUES (%s, %s)", tenant, f"Tick {i} {tenant.hex[:6]}")
        _sql("INSERT INTO wallets (tenant_id, balance) VALUES (%s, 0)", tenant)
        _sql(
            "INSERT INTO client_settings (tenant_id, account_cap_per_user, idle_expiry_days,"
            " monthly_credit_pool, fastest_schedule, updated_by, research_limit_default)"
            " VALUES (%s, 100, 7, 1000000, '3d', 'seed', 1000)",
            tenant,
        )
        for k in range(n):
            account = uuid4()
            accounts[account] = tenant
            _sql(
                "INSERT INTO accounts (id, tenant_id, domain, name, primary_lane, lanes,"
                " refresh_cadence, next_refresh_at, added_by, owner_user_id, origin)"
                " VALUES (%s, %s, %s, %s, 'salesforce', ARRAY['salesforce'], 'weekly', %s,"
                " 'tick-user', 'tick-user', 'research')",
                account,
                tenant,
                f"tick{i}-{k}-{account.hex[:6]}.example",
                f"Tick {i}-{k}",
                now - timedelta(hours=1, minutes=k),
            )
    before = _sql(
        "SELECT schedule_tick_max_starts, schedule_tick_max_per_tenant FROM platform_defaults"
    )[0]
    _sql(
        "UPDATE platform_defaults SET schedule_tick_max_starts = 10,"
        " schedule_tick_max_per_tenant = 3 WHERE id = 1"
    )
    try:
        yield {"now": now, "tenants": tenants, "accounts": accounts}
    finally:
        _sql(
            "UPDATE platform_defaults SET schedule_tick_max_starts = %s,"
            " schedule_tick_max_per_tenant = %s WHERE id = 1",
            *before,
        )
        ids = list(accounts)
        _sql(
            "UPDATE research_jobs SET status = 'canceled', finished_at = now()"
            " WHERE account_id = ANY(%s) AND status IN ('queued', 'waiting_credit_limit')",
            ids,
        )
        _sql(
            "UPDATE accounts SET refresh_cadence = 'off', next_refresh_at = NULL"
            " WHERE id = ANY(%s)",
            ids,
        )


async def test_25_due_accounts_start_at_most_10_per_tick_and_3_per_tenant(
    due_world: dict[str, Any],
) -> None:
    now: datetime = due_world["now"]
    mine: dict[UUID, UUID] = due_world["accounts"]
    started_jobs: list[UUID] = []
    ticks = 0
    while len(started_jobs) < len(mine):
        ticks += 1
        assert ticks <= 6, "the deferred accounts must start on later ticks"
        rec = Recorder()
        result = await SC.tick(now + timedelta(minutes=15 * (ticks - 1)), rec)
        assert len(rec.started) <= 10 and result["started"] + result["promoted"] <= 10
        per_tenant = Counter(t for t, _ in rec.started)
        assert max(per_tenant.values(), default=0) <= 3, per_tenant
        jobs = [j for t, j in rec.started if t in due_world["tenants"]]
        if ticks == 1:
            # all four tenants get a start in the first tick (round robin), 10 in all
            assert set(due_world["tenants"]) <= set(per_tenant)
            assert len(rec.started) == 10 and result["deferred"] >= 15
            # a deferred account keeps its next refresh (it is due again next tick)
            started_accounts = {
                r[0] for r in _sql("SELECT account_id FROM research_jobs WHERE id = ANY(%s)", jobs)
            }
            deferred = next(a for a in mine if a not in started_accounts)
            nxt = _sql("SELECT next_refresh_at FROM accounts WHERE id = %s", deferred)[0][0]
            assert nxt < now
        started_jobs += jobs
    assert ticks >= 3  # 25 accounts cannot start in fewer than three ticks of ten
    accounts = _sql("SELECT account_id FROM research_jobs WHERE id = ANY(%s)", started_jobs)
    assert Counter(UUID(str(a)) for (a,) in accounts) == Counter(dict.fromkeys(mine, 1))
