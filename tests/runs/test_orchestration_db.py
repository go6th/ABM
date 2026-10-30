"""Run bookkeeping behind the slice runner's decisions, on real Postgres (orchestration review
2026-10-30: F4 pauses are not research time, F7 finished provider calls are progress, F10 a lapsed
wait is read as "no").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.runs import lifecycle
from account_intelligence.runs.lifecycle import create_run, expire_cap_pause, move
from account_intelligence.workflow.transitions import RunStatus as S
from tests.runs.test_lifecycle_db import LIMITS, RESERVE, _create, req, tenant
from tests.tracker.world import World, admin_dsn, ledger_done, tracker

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def _run():  # type: ignore[no-untyped-def]
    t = tenant()
    run = (await _create(t, req())).run_id
    return t, run


async def test_each_wait_for_a_person_adds_one_slice_to_the_ceiling() -> None:
    t, run = await _run()
    async with tenant_txn(t) as s:
        assert await lifecycle.pause_slices(s, run) == 0
        await lifecycle.note_pause(s, run)
        await lifecycle.note_pause(s, run)
    async with tenant_txn(t) as s:
        assert await lifecycle.pause_slices(s, run) == 2


async def test_finished_provider_calls_are_counted_as_work() -> None:
    t, run = await _run()
    w = World(t, uuid4(), run, "x.example")
    async with tenant_txn(t) as s:
        before = await lifecycle.completed_calls(s, run)
    ledger_done(w)
    ledger_done(w)
    async with tenant_txn(t) as s:
        assert await lifecycle.completed_calls(s, run) == before + 2


async def test_a_lapsed_wait_is_marked_for_the_slice_that_resumes_it() -> None:
    """The token-guard pause has no request row to time out: the lapse is marked on the run with
    the number of the slice that resumes it, so that slice (and a redelivered copy of it) is
    told "no" and a later wait is not."""
    t, run = await _run()
    now = datetime.now(UTC)
    async with tenant_txn(t) as s:
        await move(s, run, S.QUEUED, S.RUNNING)
        await move(
            s,
            run,
            S.RUNNING,
            S.AWAITING_APPROVAL,
            approval_deadline=now - timedelta(minutes=1),
            awaiting_kind="cap",
        )
    sql_set_slices(run, 2)
    async with tenant_txn(t) as s:
        assert not await lifecycle.pause_lapsed(s, run, 2)  # nothing lapsed yet
        assert await expire_cap_pause(s, run, now)  # resumed RUNNING; slice 2 will run
    async with tenant_txn(t) as s:
        assert await lifecycle.pause_lapsed(s, run, 2) is True
        assert await lifecycle.pause_lapsed(s, run, 2) is True  # a redelivered copy reads it again
        assert await lifecycle.pause_lapsed(s, run, 3) is False  # a later wait is its own


def sql_set_slices(run: UUID, n: int) -> None:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute("UPDATE account_runs SET slices_used = %s WHERE id = %s", (n, run))


async def test_the_newest_spend_pause_decides_whether_a_resume_is_a_decline() -> None:
    t, run = await _run()
    now = datetime.now(UTC)
    first, second = uuid4(), uuid4()

    def event(kind: str, request: object, version: int) -> None:
        with psycopg.connect(admin_dsn(), autocommit=True) as c:
            c.execute(
                "INSERT INTO spend_limit_events (tenant_id, run_id, cap_version, event,"
                " request_id, at) VALUES (%s, %s, %s, %s, %s, %s)",
                (t, run, version, kind, request, now),
            )

    async def declined() -> bool:
        async with tenant_txn(t) as s:
            return await lifecycle.pause_declined(s, run)

    assert await declined() is False  # no pause at all
    event("pause_opened", first, 1)
    assert await declined() is False  # still open
    event("declined", first, 1)
    assert await declined() is True
    event("pause_opened", second, 2)
    event("approved", second, 2)
    assert await declined() is False  # the newest one was approved
    third = uuid4()
    event("pause_opened", third, 3)
    event("timed_out", third, 3)
    assert await declined() is True  # nobody answered


async def test_a_continuation_keeps_the_incremental_inputs_of_the_run_it_continues() -> None:
    """F12: decided again, the feed's bound could differ from the stopped run's and its feed
    call (reused at no cost) would be paid for a second time."""
    from sqlalchemy import text

    from jobs.incremental import KEY_PREVIOUS_RUN, KEY_SINCE

    t = tenant()
    base = uuid4()
    stopped = await _create(
        t, req("stopped", **{KEY_PREVIOUS_RUN: str(base), KEY_SINCE: "2026-09-01"})
    )
    # no delivered base exists for the account, so deciding afresh would give the new run nothing
    async with tenant_txn(t) as s:
        again = await create_run(
            s, t, req("continued", resume_from=str(stopped.run_id)), LIMITS, tracker(s), RESERVE
        )
        stored = await s.scalar(
            text("SELECT run_input FROM account_runs WHERE id = :r"), {"r": again.run_id}
        )
    assert stored[KEY_PREVIOUS_RUN] == str(base) and stored[KEY_SINCE] == "2026-09-01"
    assert stored["resume_from"] == str(stopped.run_id)
