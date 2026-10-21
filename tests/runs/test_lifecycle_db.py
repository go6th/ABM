"""Run lifecycle on real Postgres: creation in one transaction, transitions, finish, settlement.

Credits live on the client's monthly pool and its ledger (``credit_entries``, migration 0057):
each run carries out one research job that reserves when the run is created and settles once
when it ends. The tests give the pool 100 credits and each run a 6-credit reservation, so the
numbers read as before: 94 left while a run holds its credits.

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
    CreditJob,
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
    """A synthetic client whose monthly pool holds ``balance`` credits."""
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        t = c.execute("INSERT INTO tenants (name) VALUES ('synthetic') RETURNING id").fetchone()[0]
        c.execute(
            "INSERT INTO client_settings (tenant_id, account_cap_per_user, idle_expiry_days,"
            " monthly_credit_pool, fastest_schedule, updated_by) VALUES (%s, 34, 7, %s, '3d',"
            " 'test')",
            (t, int(balance)),
        )
    return UUID(str(t))


def req(key: str | None = None, domain: str = "acme.example", **run_input: object) -> RunRequest:
    return RunRequest(domain, "standard", key or uuid4().hex, dict(run_input))


RESERVE = CreditJob(kind="new_research", scope="full", actor="test", credits=6)


async def _create(t: UUID, r: RunRequest):  # type: ignore[no-untyped-def]
    async with tenant_txn(t) as s:
        return await create_run(s, t, r, LIMITS, tracker(s), RESERVE)


def balance(t: UUID) -> Decimal:
    """Credits left in the pool this month: the pool less settled and held credits."""
    pool = fetch("SELECT monthly_credit_pool FROM client_settings WHERE tenant_id = %s", t)[0][0]
    moved = fetch(
        "SELECT coalesce(sum(credits) FILTER (WHERE kind IN ('reserve', 'settle')), 0)"
        " - coalesce(sum(credits) FILTER (WHERE kind = 'release'), 0)"
        " FROM credit_entries WHERE tenant_id = %s",
        t,
    )[0][0]
    return Decimal(pool) - Decimal(moved)


def entries(run: UUID) -> list[tuple[str, int]]:
    """The credit entries of the run's job, in order."""
    return fetch(
        "SELECT e.kind, e.credits FROM credit_entries e JOIN research_jobs j ON j.id = e.job_id"
        " WHERE j.run_id = %s ORDER BY e.id",
        run,
    )


async def test_create_is_one_transaction_with_tracker_and_reservation() -> None:
    t = tenant()
    created = await _create(t, req(lanes_off=["veeva"]))
    run = created.run_id
    assert created.status == "QUEUED" and not created.replayed
    assert fetch("SELECT status FROM runs WHERE id = %s", run) == [("queued",)]
    assert fetch("SELECT count(*) FROM coverage_rows WHERE run_id = %s", run) == [(33,)]
    assert entries(run) == [("reserve", 6)]
    assert fetch("SELECT status, credits_estimated FROM research_jobs WHERE run_id = %s", run) == [
        ("running", 6)
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
    assert fetch("SELECT count(*) FROM research_jobs WHERE tenant_id = %s", t) == [(0,)]
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
        c.execute("UPDATE account_runs SET spent_usd = 0.15 WHERE id = %s", (run,))
    async with tenant_txn(t) as s:
        status, settlement = await finish(s, tracker(s), run, S.COMPLETED)
    assert status == "PARTIAL"
    assert fetch("SELECT count(*) FROM coverage_rows WHERE run_id = %s AND swept", run) == [(33,)]
    # the run's budget is its 6 credits in dollars (6 x 0.05); 0.15 spent is 3 credits
    assert fetch("SELECT budget_usd FROM account_runs WHERE id = %s", run) == [(Decimal("0.30"),)]
    assert (settlement.committed, settlement.refunded) == (Decimal(3), Decimal(3))
    assert balance(t) == Decimal("97")


async def test_the_pool_ledger_replays_to_what_is_left() -> None:
    """Replaying the run's entries gives the pool what is left: the reserve holds the credits,
    the settle charges what was used and the release returns the hold."""
    t = tenant()
    run = (await _create(t, req())).run_id
    await _to_verifying(t, run)
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute("UPDATE account_runs SET spent_usd = 0.15 WHERE id = %s", (run,))
    async with tenant_txn(t) as s:
        await finish(s, tracker(s), run, S.COMPLETED)
    assert entries(run) == [("reserve", 6), ("settle", 3), ("release", 6)]
    held = used = 0
    for kind, credits in entries(run):
        held += credits if kind == "reserve" else -credits if kind == "release" else 0
        used += credits if kind == "settle" else 0
    assert (held, used) == (0, 3)
    assert balance(t) == Decimal(100 - used) == Decimal("97")
    assert fetch("SELECT status, credits_used FROM research_jobs WHERE run_id = %s", run) == [
        ("done", 3)
    ]


async def test_runs_never_write_the_wallet_history() -> None:
    """The wallet tables are read-only history: the application role cannot change them."""
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        rights = c.execute(
            "SELECT has_table_privilege('app_rw', 'wallets', 'INSERT'),"
            " has_table_privilege('app_rw', 'wallets', 'UPDATE'),"
            " has_table_privilege('app_rw', 'wallet_ledger', 'INSERT'),"
            " has_table_privilege('app_rw', 'wallets', 'SELECT')"
        ).fetchone()
    assert rights == (False, False, False, True)


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
    rows = entries(run)
    assert [e for e, _ in rows] == ["reserve", "release"]  # a platform fault charges nothing
    assert dict(rows)["reserve"] == dict(rows)["release"]  # reserved = used + released
    assert fetch("SELECT status, credits_used FROM research_jobs WHERE run_id = %s", run) == [
        ("failed", 0)
    ]
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


async def test_a_run_with_a_high_row_unset_never_ends_completed() -> None:
    """The partial-on-incomplete rule (kept in the replica; MAIN's change to the ABM graph's
    deliver step that always reported "completed" was not ported): asked to end COMPLETED with one
    High coverage row never reported, the run ends PARTIAL and that row is swept with a reason."""
    from account_intelligence.tracker.config import load_checklist
    from account_intelligence.tracker.model import Actor
    from account_intelligence.workflow.spec import load_spec
    from tests.tracker.world import World

    t = tenant()
    run = (await _create(t, req())).run_id
    account = fetch("SELECT account_id FROM account_runs WHERE id = %s", run)[0][0]
    w = World(t, account, run, "acme.example")
    owners = load_spec().coverage_owners
    rows = load_checklist().rows
    high = next(r.key for r in rows if r.priority == "high")
    async with tenant_txn(t) as s:
        svc = tracker(s)
        for row in rows:
            if row.key == high:
                continue
            ref = ledger_done(w, record_count=1, queries=(row.key,))
            await svc.record_outcome(t, run, row.key, [ref], Actor.STAGE, stage=owners[row.key])
    await _to_verifying(t, run)
    async with tenant_txn(t) as s:
        status, _ = await finish(s, tracker(s), run, S.COMPLETED)
    assert status == "PARTIAL"
    assert fetch(
        "SELECT status IS NOT NULL, reason_code IS NOT NULL, swept FROM coverage_rows"
        " WHERE run_id = %s AND row_key = %s",
        run,
        high,
    ) == [(True, True, True)]


async def test_the_abm_deliver_step_still_reports_partial_when_rows_are_open() -> None:
    """A guard on the source: the ABM graph's deliver step decides "completed" only when every
    coverage row is done or none, or a stated gap the client chose (internal history not
    switched on: ``agents.guards.STATED_GAPS``, allowed the same way by the tracker's completion
    policy) (the replica keeps it; see FIX_LOG decision D1)."""
    import inspect

    from agents import graph

    source = inspect.getsource(graph.build_abm_graph)
    deliver = source[source.index("async def deliver(") : source.index("async def finish(")]
    assert 'r.status in ("done", "none") or r.reason in STATED_GAPS' in deliver
    from agents.guards import STATED_GAPS

    assert {"no_internal_access"} == STATED_GAPS
    assert '"completed" if complete else "partial"' in deliver
