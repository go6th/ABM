"""Kill tests for the mutation run (2026-10-30) of ``runs.recovery``: which hand-offs a pass
repeats, with which cut-offs and in which order, what a failure of one item or one client does to
the rest, and what is logged. The transaction, the lifecycle moves and the queue are recording
fakes (the Postgres tests of the same behaviour are ``test_recovery.py``). Hermetic."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from account_intelligence.replica import credits as CR
from account_intelligence.runs import recovery as R

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
T1 = UUID("00000000-0000-0000-0000-0000000000a1")
T2 = UUID("00000000-0000-0000-0000-0000000000a2")
J1, J2 = UUID(int=11), UUID(int=12)
R1, R2 = UUID(int=21), UUID(int=22)
W1, W2 = UUID(int=31), UUID(int=32)
JOBS_SQL = (
    "SELECT id FROM research_jobs WHERE status = 'queued' AND created_at < :b"
    " ORDER BY created_at LIMIT :n"
)
RUNS_SQL = (
    "SELECT r.id FROM account_runs r WHERE r.status = 'QUEUED'"
    " AND r.slices_used = 0 AND r.created_at < :b"
    " AND NOT EXISTS (SELECT 1 FROM research_jobs j WHERE j.run_id = r.id"
    "   AND j.status IN ('done', 'failed', 'canceled'))"
    " ORDER BY r.created_at LIMIT :n"
)
WAITS_SQL = (
    "SELECT id, owner_user_id FROM account_runs"
    " WHERE status = 'AWAITING_APPROVAL'"
    " AND approval_deadline IS NOT NULL AND approval_deadline < :late"
    " ORDER BY approval_deadline LIMIT :n"
)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return self.rows


class _World:
    """One client's database, the lifecycle moves and the queue, all recorded."""

    def __init__(self, *, jobs=(), runs=(), waits=(), requeued=()) -> None:  # type: ignore[no-untyped-def]
        self.jobs, self.runs, self.waits, self.requeued = jobs, runs, waits, requeued
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.opened: list[tuple[UUID, dict[str, Any]]] = []
        self.requeue_args: list[tuple[Any, datetime, int]] = []
        self.slices: list[tuple[UUID, UUID, int]] = []
        self.started: list[tuple[UUID, UUID]] = []
        self.expired: set[UUID] = set()
        self.paused: set[UUID] = set()
        self.used: dict[UUID, int] = {}
        self.moves: list[tuple[str, Any]] = []
        self.break_tenants: set[UUID] = set()
        self.break_jobs: set[UUID] = set()
        self.break_runs: set[UUID] = set()
        self.break_waits: set[UUID] = set()

    # the session
    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        sql = str(statement)
        self.statements.append((sql, params))
        rows = {
            JOBS_SQL: [SimpleNamespace(id=str(j)) for j in self.jobs],
            RUNS_SQL: [SimpleNamespace(id=str(r)) for r in self.runs],
            WAITS_SQL: [SimpleNamespace(id=str(w), owner_user_id=o) for w, o in self.waits],
        }
        return _Result(rows[sql])

    @asynccontextmanager
    async def tenant_txn(self, tenant: UUID, **kw: Any):  # type: ignore[no-untyped-def]
        if tenant in self.break_tenants:
            raise RuntimeError("database down")
        self.opened.append((tenant, kw))
        yield self

    async def requeue_unstarted(self, session: Any, before: datetime, limit: int) -> list[UUID]:
        self.requeue_args.append((session, before, limit))
        return list(self.requeued)

    async def start_job(self, tenant: UUID, job: UUID) -> None:
        if job in self.break_jobs:
            raise RuntimeError("worker down")
        self.started.append((tenant, job))

    async def enqueue_slice(self, tenant: UUID, run: UUID, n: int, delay_s: float = 0) -> None:
        if run in self.break_runs:
            raise RuntimeError("queue down")
        self.slices.append((tenant, run, n))

    async def expire_approval(self, s: Any, tracker: Any, run: UUID, now: datetime) -> bool:
        assert s is self  # the lifecycle moves run in the wait owner's own transaction
        if run in self.break_waits:
            raise RuntimeError("lifecycle down")
        self.moves.append(("approval", (tracker, run, now)))
        return run in self.expired

    async def expire_cap_pause(self, s: Any, run: UUID, now: datetime) -> bool:
        assert s is self
        self.moves.append(("pause", (run, now)))
        return run in self.paused

    async def slices_used(self, s: Any, run: UUID) -> int:
        assert s is self
        self.moves.append(("used", run))
        return self.used[run]


def _recover(
    world: _World, monkeypatch: pytest.MonkeyPatch, tenants=(T1,), **kw: Any
) -> R.RecoveryResult:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(R, "tenant_txn", world.tenant_txn)
    monkeypatch.setattr(CR, "requeue_unstarted", world.requeue_unstarted)
    monkeypatch.setattr(R.lifecycle, "expire_approval", world.expire_approval)
    monkeypatch.setattr(R.lifecycle, "expire_cap_pause", world.expire_cap_pause)
    monkeypatch.setattr(R.lifecycle, "slices_used", world.slices_used)
    scheduler = SimpleNamespace(enqueue_slice=world.enqueue_slice)
    return asyncio.run(
        R.recover(list(tenants), world.start_job, scheduler, lambda s: ("tracker", s), NOW, **kw)  # type: ignore[arg-type]
    )


# ---- the result -------------------------------------------------------------------------------


def test_the_total_counts_started_jobs_and_runs_and_both_kinds_of_wait() -> None:
    out = R.RecoveryResult(
        jobs_started=[J1],
        jobs_requeued=[J1, J2],
        runs_started=[R1, R2],
        waits_cancelled=[W1],
        waits_resumed=[W1, W2],
        failed=[J2],
    )
    assert out.total == 1 + 2 + 1 + 2
    assert R.RecoveryResult().total == 0


# ---- one pass ---------------------------------------------------------------------------------


def test_a_pass_starts_what_was_committed_and_never_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World(
        jobs=[J1, J2], runs=[R1], waits=[(W1, "owner-1"), (W2, "owner-2")], requeued=[J2]
    )
    world.expired = {W1}
    world.paused = {W2}
    world.used = {W2: 4}
    out = _recover(world, monkeypatch)
    assert out.jobs_started == [J1, J2] and out.jobs_requeued == [J2]
    assert out.runs_started == [R1]
    assert out.waits_cancelled == [W1] and out.waits_resumed == [W2]
    assert out.failed == [] and out.total == 5
    assert world.started == [(T1, J1), (T1, J2)]
    assert world.slices == [
        (T1, R1, 0),
        (T1, W2, 4),
    ]  # the run's first slice; the paused run's next


def test_the_statements_and_their_cut_offs() -> None:
    mp = pytest.MonkeyPatch()
    try:
        world = _World()
        _recover(world, mp, after_s=100.0, grace_s=250.0, limit=7)
    finally:
        mp.undo()
    before, late = NOW - timedelta(seconds=100), NOW - timedelta(seconds=250)
    assert world.statements == [
        (JOBS_SQL, {"b": before, "n": 7}),
        (RUNS_SQL, {"b": before, "n": 7}),
        (WAITS_SQL, {"late": late, "n": 7}),
    ]
    assert world.requeue_args == [(world, before, 7)]  # the jobs put back come first
    assert world.opened == [(T1, {"system": True})]


def test_the_defaults_are_ten_and_fifteen_minutes_and_fifty_items() -> None:
    mp = pytest.MonkeyPatch()
    try:
        world = _World()
        _recover(world, mp)
    finally:
        mp.undo()
    assert world.statements[0][1] == {"b": NOW - timedelta(seconds=600), "n": 50}
    assert world.statements[2][1] == {"late": NOW - timedelta(seconds=900), "n": 50}


def test_a_wait_is_expired_as_its_owner_and_a_resumed_one_asks_for_its_slice_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World(waits=[(W1, "owner-1"), (W2, "owner-2")])
    world.expired = {W1}
    world.paused = {W2}
    world.used = {W2: 3}
    _recover(world, monkeypatch)
    waits_opened = [o for o in world.opened if "user" in o[1]]
    assert waits_opened == [
        (T1, {"user": "owner-1", "system": True}),
        (T1, {"user": "owner-2", "system": True}),
    ]
    assert world.moves == [
        ("approval", (("tracker", world), W1, NOW)),
        ("approval", (("tracker", world), W2, NOW)),
        ("pause", (W2, NOW)),
        ("used", W2),
    ]  # an expired wait is not also resumed; the count is read only for a resumed one


def test_a_wait_neither_expired_nor_paused_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(waits=[(W1, "owner-1")])
    out = _recover(world, monkeypatch)
    assert out.waits_cancelled == [] and out.waits_resumed == [] and out.total == 0
    assert world.slices == [] and ("used", W1) not in world.moves


def test_a_failed_hand_off_is_listed_and_the_rest_still_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    world = _World(jobs=[J1, J2], runs=[R1, R2], waits=[(W1, "o1"), (W2, "o2")])
    world.break_jobs, world.break_runs, world.break_waits = {J1}, {R1}, {W1}
    world.expired = {W2}
    with caplog.at_level(logging.INFO, logger=R.__name__):
        out = _recover(world, monkeypatch)
    assert out.failed == [J1, R1, W1]
    assert out.jobs_started == [J2] and out.runs_started == [R2] and out.waits_cancelled == [W2]
    events = {r.getMessage(): r for r in caplog.records}
    assert events["recovery.job_failed"].error_class == "RuntimeError"
    assert events["recovery.run_failed"].error_class == "RuntimeError"
    assert events["recovery.wait_failed"].error_class == "RuntimeError"
    assert [r.levelno for r in caplog.records if r.getMessage().endswith("_failed")] == [
        logging.WARNING
    ] * 3


def test_a_client_that_fails_does_not_stop_the_pass(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    world = _World(jobs=[J1])
    world.break_tenants = {T1}
    with caplog.at_level(logging.INFO, logger=R.__name__):
        out = _recover(world, monkeypatch, tenants=(T1, T2))
    assert out.jobs_started == [J1] and world.started == [(T2, J1)]
    failed = [r for r in caplog.records if r.getMessage() == "recovery.client_failed"]
    assert len(failed) == 1 and failed[0].levelno == logging.WARNING
    assert (failed[0].tenant_id, failed[0].error_class) == (str(T1), "RuntimeError")


def test_the_clients_are_visited_in_order_each_with_its_own_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World(jobs=[J1], requeued=[J2])
    out = _recover(world, monkeypatch, tenants=(T2, T1))
    assert world.started == [(T2, J1), (T1, J1)]
    assert out.jobs_requeued == [J2, J2]  # every client's requeued jobs are added up


def test_a_pass_logs_one_line_with_the_counts_only_when_it_did_something(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    world = _World(jobs=[J1], runs=[R1, R2], waits=[(W1, "o1"), (W2, "o2")])
    world.expired = {W1}
    world.paused = {W2}
    world.used = {W2: 1}
    with caplog.at_level(logging.INFO, logger=R.__name__):
        _recover(world, monkeypatch)
    line = next(r for r in caplog.records if r.getMessage() == "recovery.handoffs")
    assert (line.count, line.jobs, line.runs, line.waits) == (5, 1, 2, 2)
    assert line.levelno == logging.INFO
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=R.__name__):
        _recover(_World(), monkeypatch)
    assert [r for r in caplog.records if r.getMessage() == "recovery.handoffs"] == []
