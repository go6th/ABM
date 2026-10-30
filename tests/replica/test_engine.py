"""The production research engine bridge: a confirmed job becomes a run on the run lifecycle with
the job's scope, slice 0 goes to the worker, and the job settles once when the run ends
(SYNTHETIC tenant and domain; the graph itself is exercised by the system tests).

GUARANTEE(test_a_confirmed_refresh_runs_on_the_lifecycle_and_settles_when_the_run_ends)
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import credits as CR
from account_intelligence.replica import jobs
from account_intelligence.runs import lifecycle
from account_intelligence.workflow.transitions import RunStatus as S
from tests.conftest import ADMIN_DSN
from tests.runs.test_lifecycle_db import _to_verifying, tenant
from tests.tracker.world import fetch, tracker

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _container():  # type: ignore[no-untyped-def]
    from account_intelligence.app.container import build_container

    return build_container({"APP_ENV": "test"})


async def test_a_confirmed_refresh_runs_on_the_lifecycle_and_settles_when_the_run_ends() -> None:
    t = tenant("1000")
    now = datetime.now(UTC)
    async with tenant_txn(t) as s:
        account, _ = await lifecycle.add_account(s, t, "scoped.example", added_by="u1")
        job_id, status = await CR.reserve(
            s, account_id=account, kind="web_refresh", scope="jobs", credits=20, actor="u1", now=now
        )
    assert status == "queued"
    container = _container()
    outcome = await jobs.run_job(t, job_id, jobs.GraphEngine(container))
    assert outcome == "started"
    ((run_id, run_input, budget, cap_required),) = fetch(
        "SELECT r.id, r.run_input, r.budget_usd, r.cap_required FROM account_runs r"
        " JOIN research_jobs j ON j.run_id = r.id WHERE j.id = %s",
        job_id,
    )
    assert run_input["scope"] == "jobs" and run_input["job_id"] == str(job_id)
    assert set(run_input["scope_rows"]) == set(jobs.scope_rows()["jobs"])
    assert budget == Decimal("1.0000") and cap_required is False  # 20 credits x 0.05 USD
    assert container.scheduler.queued[-1][1:] == (run_id, 0)
    assert fetch("SELECT status FROM research_jobs WHERE id = %s", job_id) == [("running",)]
    # a redelivered task changes nothing: the job is no longer queued
    assert await jobs.run_job(t, job_id, jobs.GraphEngine(container)) == "skipped"
    assert fetch("SELECT count(*) FROM account_runs WHERE tenant_id = %s", t) == [(1,)]

    await _to_verifying(t, run_id)
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("UPDATE account_runs SET spent_usd = 0.42 WHERE id = %s", (run_id,))
    async with tenant_txn(t) as s:
        await lifecycle.finish(s, tracker(s), run_id, S.PARTIAL, reason="test")
    assert fetch(
        "SELECT status, credits_used, stop_reason FROM research_jobs WHERE id = %s", job_id
    ) == [("done", 9, None)]  # ceil(0.42 / 0.05)
    assert fetch("SELECT kind FROM notifications WHERE job_id = %s AND user_id = 'u1'", job_id) == [
        ("refresh_finished",)
    ]


async def test_a_failure_before_the_run_exists_releases_the_reserve() -> None:
    t = tenant("1000")
    now = datetime.now(UTC)
    async with tenant_txn(t) as s:
        account, _ = await lifecycle.add_account(s, t, "gone.example", added_by="u1")
        job_id, _ = await CR.reserve(
            s, account_id=account, kind="web_refresh", scope="full", credits=30, actor="u1", now=now
        )
    assert await jobs.run_job(t, job_id, jobs.SkillEngine()) == "failed"
    assert fetch(
        "SELECT kind, credits FROM credit_entries WHERE job_id = %s ORDER BY id", job_id
    ) == [
        ("reserve", 30),
        ("release", 30),
    ]


async def test_every_scope_names_real_rows_and_the_engine_choice_is_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scopes = jobs.scope_rows()
    assert set(scopes) >= set(CR.SCOPES) and scopes["full"] == ()
    monkeypatch.delenv("RESEARCH_ENGINE", raising=False)
    assert jobs.engine_choice("test") == "hermetic"
    assert jobs.engine_choice("production") == "graph"
    monkeypatch.setenv("RESEARCH_ENGINE", "hermetic")
    with pytest.raises(RuntimeError):
        jobs.engine_choice("production")
    monkeypatch.setenv("RESEARCH_ENGINE", "graph")
    assert jobs.engine_state("production").startswith("abm_graph (skill package not installed")


async def test_a_lost_slice_zero_enqueue_leaves_the_run_pending_for_the_sweeper() -> None:
    """The run was committed and linked to the job, then the queue refused slice 0: the job must
    not settle failed (its credits released, the run QUEUED for ever); the run waits for
    ``runs.recovery`` (orchestration review F6, case 3)."""
    t = tenant("1000")
    now = datetime.now(UTC)
    async with tenant_txn(t) as s:
        account, _ = await lifecycle.add_account(s, t, "queue-down.example", added_by="u1")
        job_id, _ = await CR.reserve(
            s, account_id=account, kind="web_refresh", scope="jobs", credits=20, actor="u1", now=now
        )
    container = _container()

    async def refuse(*_: object, **__: object) -> None:
        raise ConnectionError("queue unreachable")

    container.scheduler.enqueue_slice = refuse  # type: ignore[method-assign]
    assert await jobs.run_job(t, job_id, jobs.GraphEngine(container)) == "started"
    ((status, run_id),) = fetch("SELECT status, run_id FROM research_jobs WHERE id = %s", job_id)
    assert status == "running" and run_id is not None
    assert fetch("SELECT status, slices_used FROM account_runs WHERE id = %s", run_id) == [
        ("QUEUED", 0)
    ]
    assert fetch("SELECT kind FROM credit_entries WHERE job_id = %s ORDER BY id", job_id) == [
        ("reserve",)
    ]  # nothing released


async def test_a_queue_that_cannot_be_reached_never_fails_the_caller_of_a_committed_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Down:
        async def enqueue_research_job(self, *_: object) -> None:
            raise ConnectionError("queue unreachable")

    monkeypatch.setattr(jobs, "_scheduler", Down())
    await jobs.run_or_enqueue(tenant(), uuid4())  # does not raise
