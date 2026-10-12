"""Run lifecycle error paths on real Postgres: concurrent creation, refused moves, settlement of a
missing or live run, a run without a budget, and the timeout paths that must not fire.

Each test names the money or state rule it protects; none touches another tenant.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.runs.lifecycle import (
    LifecycleError,
    RunNotFound,
    RunStateConflict,
    create_run,
    expire_approval,
    expire_cap_pause,
    finish,
    move,
    settle_once,
)
from account_intelligence.workflow.transitions import RunStatus as S
from tests.runs.test_lifecycle_db import LIMITS, _create, balance, req, tenant
from tests.tracker.world import admin_dsn, fetch, tracker

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def test_concurrent_create_with_one_key_reserves_once() -> None:
    """Two requests with one idempotency key: the second waits on the first's insert, then
    loses it and is told to retry; exactly one run and one reservation exist."""
    t = tenant()
    request = req("race-key")
    first_ready = asyncio.Event()
    release = asyncio.Event()

    async def first() -> object:
        async with tenant_txn(t) as s:
            created = await create_run(s, t, request, LIMITS, tracker(s))
            first_ready.set()
            await release.wait()  # hold the uncommitted insert while the second one runs
            return created

    async def second() -> object:
        await first_ready.wait()
        async with tenant_txn(t) as s:
            return await create_run(s, t, request, LIMITS, tracker(s))

    a = asyncio.create_task(first())
    b = asyncio.create_task(second())
    await asyncio.wait_for(first_ready.wait(), 10)
    await asyncio.sleep(0.3)  # the second request is now blocked on the unique key
    release.set()
    created = await a
    with pytest.raises(RunStateConflict):
        await b
    runs = fetch("SELECT id FROM account_runs WHERE tenant_id = %s", t)
    assert [str(r[0]) for r in runs] == [str(created.run_id)]  # type: ignore[attr-defined]
    assert fetch("SELECT count(*) FROM wallet_ledger WHERE tenant_id = %s", t) == [(1,)]
    assert balance(t) == Decimal("94")
    replay = await _create(t, request)  # the retry the error asks for reads the winner
    assert replay.replayed and replay.run_id == created.run_id  # type: ignore[attr-defined]


async def test_terminal_states_and_unknown_wait_reasons_are_refused_by_move() -> None:
    t = tenant()
    run = (await _create(t, req())).run_id
    with pytest.raises(LifecycleError, match="through finish"):
        async with tenant_txn(t) as s:
            await move(s, run, S.QUEUED, S.CANCELED)
    with pytest.raises(LifecycleError, match="unknown awaiting kind"):
        async with tenant_txn(t) as s:
            await move(s, run, S.QUEUED, S.RUNNING, awaiting_kind="coffee")
    assert fetch("SELECT status FROM account_runs WHERE id = %s", run) == [("QUEUED",)]
    assert balance(t) == Decimal("94")  # nothing settled by a refused move


async def test_settlement_of_a_missing_or_live_run_is_refused() -> None:
    t = tenant()
    run = (await _create(t, req())).run_id
    with pytest.raises(RunNotFound):
        async with tenant_txn(t) as s:
            await settle_once(s, uuid4())
    with pytest.raises(LifecycleError, match="only a terminal run"):
        async with tenant_txn(t) as s:
            await settle_once(s, run)
    other = tenant()
    with pytest.raises(RunNotFound):  # another tenant's run is simply not there (RLS)
        async with tenant_txn(other) as s:
            await settle_once(s, run)
    assert fetch("SELECT entry FROM wallet_ledger WHERE run_id = %s", run) == [("reserve",)]


async def test_a_run_without_a_budget_commits_nothing_and_refunds_the_reserve() -> None:
    """``used`` is spent / budget; with no budget nothing was usable, so nothing is committed."""
    t = tenant()
    run = (await _create(t, req())).run_id
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute("UPDATE account_runs SET budget_usd = NULL, spent_usd = 0 WHERE id = %s", (run,))
    async with tenant_txn(t) as s:
        await move(s, run, S.QUEUED, S.RUNNING)
        await move(s, run, S.RUNNING, S.DISTILLING)
        await move(s, run, S.DISTILLING, S.VERIFYING)
        _, settlement = await finish(s, tracker(s), run, S.PARTIAL, reason="test")
    assert (settlement.committed, settlement.refunded) == (Decimal(0), Decimal("6.0000"))
    assert balance(t) == Decimal("100")


async def test_cap_pause_never_expires_through_the_approval_timeout() -> None:
    """A pause instead of fail (``awaiting_kind = cap``) is not cancelled by the approval
    timeout; and the cap-pause expiry ignores a run that is not waiting."""
    t = tenant()
    run = (await _create(t, req())).run_id
    now = datetime.now(UTC)
    async with tenant_txn(t) as s:
        assert not await expire_cap_pause(s, run, now)  # QUEUED: nothing to expire
        assert not await expire_cap_pause(s, uuid4(), now)
        await move(s, run, S.QUEUED, S.RUNNING)
        await move(
            s, run, S.RUNNING, S.AWAITING_APPROVAL, approval_deadline=now, awaiting_kind="cap"
        )
    async with tenant_txn(t) as s:
        assert not await expire_approval(s, tracker(s), run, now + timedelta(hours=1))
    assert fetch("SELECT status, awaiting_kind FROM account_runs WHERE id = %s", run) == [
        ("AWAITING_APPROVAL", "cap")
    ]
    assert balance(t) == Decimal("94")  # still reserved: no settlement happened
