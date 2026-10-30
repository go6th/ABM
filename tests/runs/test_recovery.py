"""Hand-off recovery on real Postgres (orchestration review 2026-10-30, F6): what was committed
but never handed to the worker is started again by the sweeper, and nothing that is healthy or
finished is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.runs.lifecycle import move
from account_intelligence.runs.recovery import recover
from account_intelligence.workflow.transitions import RunStatus as S
from tests.runs.test_lifecycle_db import _create, balance, req, tenant
from tests.tracker.world import admin_dsn, fetch, tracker

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


class Scheduler:
    """Records the slices handed to the queue."""

    def __init__(self) -> None:
        self.slices: list[tuple[UUID, int]] = []

    async def enqueue_slice(self, tenant_id: Any, run_id: UUID, n: int, delay_s: float = 0) -> None:
        self.slices.append((run_id, n))

    async def schedule_timeout(self, *a: Any) -> None:
        return None


class Jobs:
    """Records the jobs handed to the worker."""

    def __init__(self) -> None:
        self.started: list[UUID] = []

    async def __call__(self, tenant_id: Any, job_id: UUID) -> None:
        self.started.append(job_id)


def sql(statement: str, *params: object) -> None:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(statement, params)


def old(run: UUID) -> None:
    sql("UPDATE account_runs SET created_at = now() - interval '1 hour' WHERE id = %s", run)


def tracker_for(s: Any) -> Any:
    return tracker(s)


async def run_recover(t: UUID, scheduler: Scheduler, jobs: Jobs, **kw: Any) -> Any:
    return await recover([t], jobs, scheduler, tracker_for, datetime.now(UTC), **kw)


def job_row(t: UUID, run: UUID, status: str, *, run_linked: bool, started_ago: str | None) -> UUID:
    """A job of the run's account in the given state (inserted the way the credit ledger does)."""
    account = fetch("SELECT account_id FROM account_runs WHERE id = %s", run)[0][0]
    job = uuid4()
    sql(
        "INSERT INTO research_jobs (id, tenant_id, account_id, kind, scope, status, period,"
        " credits_estimated, requested_by, created_at, started_at, run_id)"
        " VALUES (%s, %s, %s, 'new_research', 'full', %s, to_char(now(), 'YYYY-MM'), 6, 'test',"
        " now() - interval '1 hour', "
        + ("now() - interval '" + started_ago + "'" if started_ago else "NULL")
        + ", %s)",
        job,
        t,
        account,
        status,
        run if run_linked else None,
    )
    return job


async def test_a_job_that_was_committed_but_never_started_is_started_again() -> None:
    t = tenant()
    run = (await _create(t, req())).run_id
    queued = job_row(t, run, "queued", run_linked=False, started_ago=None)
    fresh = job_row(t, run, "queued", run_linked=False, started_ago=None)
    sql("UPDATE research_jobs SET created_at = now() WHERE id = %s", fresh)  # not stuck yet
    jobs, scheduler = Jobs(), Scheduler()
    out = await run_recover(t, scheduler, jobs)
    assert jobs.started == [queued] and out.jobs_started == [queued]


async def test_a_job_claimed_by_a_worker_that_died_before_its_run_is_queued_again() -> None:
    t = tenant()
    run = (await _create(t, req())).run_id
    dead = job_row(t, run, "running", run_linked=False, started_ago="1 hour")
    live = job_row(t, run, "running", run_linked=False, started_ago="1 minute")
    jobs, scheduler = Jobs(), Scheduler()
    out = await run_recover(t, scheduler, jobs)
    assert out.jobs_requeued == [dead] and jobs.started == [dead]
    assert fetch("SELECT status FROM research_jobs WHERE id = %s", dead) == [("queued",)]
    assert fetch("SELECT status FROM research_jobs WHERE id = %s", live) == [("running",)]


async def test_a_run_that_never_began_slice_zero_is_started_again() -> None:
    t = tenant()
    run = (await _create(t, req())).run_id
    scheduler, jobs = Scheduler(), Jobs()
    out = await run_recover(t, scheduler, jobs)  # created just now: not stuck
    assert scheduler.slices == [] and out.runs_started == []
    old(run)
    out = await run_recover(t, scheduler, jobs)
    assert scheduler.slices == [(run, 0)] and out.runs_started == [run]
    # a run that began its first slice, or whose job already ended, is left alone
    scheduler = Scheduler()
    sql("UPDATE account_runs SET slices_used = 1 WHERE id = %s", run)
    await run_recover(t, scheduler, jobs)
    assert scheduler.slices == []
    sql("UPDATE account_runs SET slices_used = 0 WHERE id = %s", run)
    sql("UPDATE research_jobs SET status = 'failed', finished_at = now() WHERE run_id = %s", run)
    await run_recover(t, scheduler, jobs)
    assert scheduler.slices == []


async def _waiting(kind: str, deadline_ago: timedelta) -> tuple[UUID, UUID]:
    t = tenant()
    run = (await _create(t, req())).run_id
    async with tenant_txn(t) as s:
        await move(s, run, S.QUEUED, S.RUNNING)
        await move(
            s,
            run,
            S.RUNNING,
            S.AWAITING_APPROVAL,
            approval_deadline=datetime.now(UTC) - deadline_ago,
            awaiting_kind=kind,
        )
    sql("UPDATE account_runs SET slices_used = 2 WHERE id = %s", run)
    return t, run


async def test_a_wait_past_its_deadline_is_expired_by_the_sweeper() -> None:
    """The timeout task was never created (or never ran): a first-cap approval is cancelled and
    refunded; a wait that is not yet late is left for its task."""
    t, run = await _waiting("approval", timedelta(hours=2))
    _, early = await _waiting("approval", timedelta(minutes=1))
    scheduler, jobs = Scheduler(), Jobs()
    out = await run_recover(t, scheduler, jobs)
    assert out.waits_cancelled == [run]
    assert fetch("SELECT status, finished_reason FROM account_runs WHERE id = %s", run) == [
        ("CANCELED", "approval_timeout")
    ]
    assert balance(t) == 100  # the reservation went back to the pool
    assert early not in out.waits_cancelled


async def test_a_cap_pause_past_its_deadline_resumes_the_run_with_its_next_slice() -> None:
    t, run = await _waiting("cap", timedelta(hours=2))
    scheduler, jobs = Scheduler(), Jobs()
    out = await run_recover(t, scheduler, jobs)
    assert out.waits_resumed == [run] and scheduler.slices == [(run, 2)]
    assert fetch("SELECT status FROM account_runs WHERE id = %s", run) == [("RUNNING",)]
    # and the resumed graph will read the lapse as "no" (F10)
    assert fetch("SELECT run_input->>'pause_lapsed' FROM account_runs WHERE id = %s", run) == [
        ("2",)  # the number of the slice that resumes the run
    ]


async def test_a_failing_client_does_not_stop_the_pass_for_the_others() -> None:
    t = tenant()
    run = (await _create(t, req())).run_id
    old(run)
    scheduler, jobs = Scheduler(), Jobs()
    out = await recover([uuid4(), t], jobs, scheduler, tracker_for, datetime.now(UTC))
    assert out.runs_started == [run]
