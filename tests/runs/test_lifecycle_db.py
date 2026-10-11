"""Run lifecycle on real Postgres: creation in one transaction, transitions, finish, settlement.

GUARANTEE(test_create_is_one_transaction_with_tracker_and_reservation)
GUARANTEE(test_settlement_is_exactly_once_and_balances)
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.runs.lifecycle import (
    IdempotencyConflict,
    InsufficientCredits,
    RunLimits,
    RunRequest,
    RunStateConflict,
    create_run,
    expire_approval,
    finish,
    move,
    settle_once,
)
from account_intelligence.workflow.transitions import RunStatus as S
from tests.tracker.world import admin_dsn, fetch, ledger_done, tracker

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
LIMITS = RunLimits("test-graph", Decimal("5.00"), 60, Decimal("6.0"))


def tenant(balance: str = "100") -> UUID:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        t = c.execute("INSERT INTO tenants (name) VALUES ('synthetic') RETURNING id").fetchone()[0]
        c.execute("INSERT INTO wallets (tenant_id, balance) VALUES (%s, %s)", (t, balance))
    return UUID(str(t))


def req(key: str | None = None, domain: str = "acme.example", **run_input: object) -> RunRequest:
    return RunRequest(domain, "standard", key or uuid4().hex, dict(run_input))


async def _create(t: UUID, r: RunRequest):  # type: ignore[no-untyped-def]
    async with tenant_txn(t) as s:
        return await create_run(s, t, r, LIMITS, tracker(s))


def balance(t: UUID) -> Decimal:
    return Decimal(fetch("SELECT balance FROM wallets WHERE tenant_id = %s", t)[0][0])


async def test_create_is_one_transaction_with_tracker_and_reservation() -> None:
    t = tenant()
    created = await _create(t, req(lanes_off=["veeva"]))
    run = created.run_id
    assert created.status == "QUEUED" and not created.replayed
    assert fetch("SELECT status FROM runs WHERE id = %s", run) == [("queued",)]
    assert fetch("SELECT count(*) FROM coverage_rows WHERE run_id = %s", run) == [(33,)]
    assert fetch("SELECT entry, amount FROM wallet_ledger WHERE run_id = %s", run) == [
        ("reserve", Decimal("-6.0000"))
    ]
    assert balance(t) == Decimal("94")
    assert fetch("SELECT run_input FROM account_runs WHERE id = %s", run)[0][0] == {
        "lanes_off": ["veeva"]
    }


async def test_idempotency_same_body_replays_different_body_conflicts() -> None:
    t = tenant()
    first = await _create(t, req("key-1"))
    again = await _create(t, req("key-1"))
    assert again.replayed and again.run_id == first.run_id and balance(t) == Decimal("94")
    with pytest.raises(IdempotencyConflict):
        await _create(t, req("key-1", domain="other.example"))


async def test_insufficient_credits_leaves_nothing_behind() -> None:
    t = tenant("1")
    with pytest.raises(InsufficientCredits):
        await _create(t, req("poor"))
    assert fetch("SELECT count(*) FROM account_runs WHERE tenant_id = %s", t) == [(0,)]
    assert fetch("SELECT count(*) FROM runs WHERE tenant_id = %s", t) == [(0,)]
    assert balance(t) == Decimal("1")


async def test_transitions_follow_the_table_and_races_lose_cleanly() -> None:
    t = tenant()
    run = (await _create(t, req())).run_id
    async with tenant_txn(t) as s:
        await move(s, run, S.QUEUED, S.RUNNING)
    assert fetch("SELECT status FROM runs WHERE id = %s", run) == [("running",)]
    with pytest.raises(RunStateConflict):
        async with tenant_txn(t) as s:
            await move(s, run, S.QUEUED, S.RUNNING)  # already moved
    with pytest.raises(ValueError):
        async with tenant_txn(t) as s:
            await move(s, run, S.RUNNING, S.VERIFYING)  # skips DISTILLING: illegal


async def _to_verifying(t: UUID, run: UUID) -> None:
    async with tenant_txn(t) as s:
        await move(s, run, S.QUEUED, S.RUNNING)
        await move(s, run, S.RUNNING, S.DISTILLING)
        await move(s, run, S.DISTILLING, S.VERIFYING)


async def test_finish_with_gaps_is_partial_swept_and_settled_proportionally() -> None:
    t = tenant()
    run = (await _create(t, req())).run_id
    await _to_verifying(t, run)
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute("UPDATE account_runs SET spent_usd = 2.5 WHERE id = %s", (run,))
    async with tenant_txn(t) as s:
        status, settlement = await finish(s, tracker(s), run, S.COMPLETED)
    assert status == "PARTIAL"
    assert fetch("SELECT count(*) FROM coverage_rows WHERE run_id = %s AND swept", run) == [(33,)]
    assert (settlement.committed, settlement.refunded) == (Decimal("3.0000"), Decimal("3.0000"))
    assert balance(t) == Decimal("97")


async def test_ledger_balance_after_replays_row_by_row() -> None:
    """Replaying the ledger row by row reproduces every balance_after. The reserve moved the
    money out, so a commit records its amount but moves nothing; the refund moves money back. The
    commit row once carried the post-refund balance."""
    t = tenant()
    run = (await _create(t, req())).run_id
    await _to_verifying(t, run)
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute("UPDATE account_runs SET spent_usd = 2.5 WHERE id = %s", (run,))
    async with tenant_txn(t) as s:
        await finish(s, tracker(s), run, S.COMPLETED)
    rows = fetch(
        "SELECT entry, amount, balance_after FROM wallet_ledger WHERE run_id = %s ORDER BY id", run
    )
    assert [e for e, _, _ in rows] == ["reserve", "commit", "refund"]
    running = Decimal("100")
    for entry, amount, after in rows:
        running += 0 if entry == "commit" else amount
        assert after == running, entry
    assert running == balance(t) == Decimal("97")


async def test_settlement_is_exactly_once_and_balances() -> None:
    t = tenant()
    run = (await _create(t, req())).run_id
    async with tenant_txn(t) as s:
        await move(s, run, S.QUEUED, S.RUNNING)
        await finish(s, tracker(s), run, S.FAILED, reason="worker crashed")

    async def again() -> object:
        async with tenant_txn(t) as s:
            return await settle_once(s, run)

    results = await asyncio.gather(*(again() for _ in range(5)))
    assert all(r.replayed for r in results)  # type: ignore[attr-defined]
    rows = fetch("SELECT entry, amount FROM wallet_ledger WHERE run_id = %s ORDER BY id", run)
    entries = {e: a for e, a in rows}
    assert [e for e, _ in rows] == ["reserve", "commit", "refund"]
    assert -entries["reserve"] == entries["commit"] + entries["refund"]  # reserved = c + r
    assert entries["commit"] == 0  # a platform fault refunds everything
    assert balance(t) == Decimal("100")


async def test_approval_timeout_cancels_and_refunds_once() -> None:
    from datetime import UTC, datetime, timedelta

    t = tenant()
    run = (await _create(t, req())).run_id
    now = datetime.now(UTC)
    async with tenant_txn(t) as s:
        await move(s, run, S.QUEUED, S.RUNNING)
        await move(s, run, S.RUNNING, S.AWAITING_APPROVAL, approval_deadline=now)
    assert fetch("SELECT status FROM runs WHERE id = %s", run) == [("paused",)]
    async with tenant_txn(t) as s:
        assert not await expire_approval(s, tracker(s), run, now - timedelta(hours=1))
        assert await expire_approval(s, tracker(s), run, now + timedelta(seconds=1))
        assert not await expire_approval(s, tracker(s), run, now + timedelta(seconds=2))
    assert fetch("SELECT status, finished_reason FROM account_runs WHERE id = %s", run) == [
        ("CANCELED", "approval_timeout")
    ]
    assert balance(t) == Decimal("100")


async def test_completed_when_every_row_is_reported() -> None:
    from account_intelligence.tracker.config import load_checklist
    from account_intelligence.tracker.model import Actor
    from account_intelligence.workflow.spec import load_spec
    from tests.tracker.world import World

    t = tenant()
    run = (await _create(t, req())).run_id
    account = fetch("SELECT account_id FROM account_runs WHERE id = %s", run)[0][0]
    w = World(t, account, run, "acme.example")
    owners = load_spec().coverage_owners
    async with tenant_txn(t) as s:
        svc = tracker(s)
        for row in load_checklist().rows:
            ref = ledger_done(w, record_count=1, queries=(row.key,))
            await svc.record_outcome(t, run, row.key, [ref], Actor.STAGE, stage=owners[row.key])
    await _to_verifying(t, run)
    async with tenant_txn(t) as s:
        status, settlement = await finish(s, tracker(s), run, S.COMPLETED)
    assert status == "COMPLETED" and settlement.committed == 0  # nothing spent in this fixture
    assert fetch("SELECT status FROM runs WHERE id = %s", run) == [("completed",)]
