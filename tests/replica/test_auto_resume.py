"""A research continues itself (``replica.auto_resume``, end-to-end automation 2026-10-16): the
scheduled sweep continues a stalled run, and a run its worker lost, the way Resume does; once per
stop (idempotent), bounded per chain, never for a stop the owner must fix, never past the window,
never under a live lease; the owner is told and the audit trail records it. SYNTHETIC data only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from tests.replica.conftest import _plane
from tests.replica.test_resume_research import _db, _run

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _job(
    tenant: UUID,
    run: UUID,
    user: str,
    *,
    status: str = "running",
    resume_from: UUID | None = None,
    request_id: UUID | None = None,
) -> UUID:
    """The research job behind a run (the sweep continues only runs a job started)."""
    (account,) = _db("SELECT account_id FROM account_runs WHERE id = %s", run)[0]
    finished = "now()" if status in ("done", "failed") else "NULL"
    (job,) = _db(
        "INSERT INTO research_jobs (tenant_id, account_id, kind, scope, status, period,"
        " credits_estimated, requested_by, owner_user_id, run_id, created_at, request_id,"
        " resume_from, finished_at) VALUES (%s, %s, 'new_research', 'full', %s,"
        " to_char(now(), 'YYYY-MM'), 5, %s, %s, %s, now() - interval '1 hour', %s, %s,"
        f" {finished}) RETURNING id",
        tenant,
        account,
        status,
        user,
        user,
        run,
        request_id,
        resume_from,
    )[0]
    return UUID(str(job))


def _continuations(run: UUID) -> list[Any]:
    return _db(
        "SELECT id, status, requested_by, request_id FROM research_jobs WHERE resume_from = %s",
        run,
    )


def _kinds(tenant: UUID, user: str) -> list[str]:
    return [
        str(k)
        for (k,) in _db(
            "SELECT kind FROM notifications WHERE tenant_id = %s AND user_id = %s"
            " ORDER BY created_at",
            tenant,
            user,
        )
    ]


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[UUID, UUID]]:
    """What the pass handed to the worker (the real scheduler is not wired in tests)."""
    from account_intelligence.replica import jobs

    calls: list[tuple[UUID, UUID]] = []

    async def record(tenant_id: UUID, job_id: UUID) -> None:
        calls.append((tenant_id, job_id))

    monkeypatch.setattr(jobs, "run_or_enqueue", record)
    return calls


def _owners(tenant: UUID, user: str, world: dict[str, Any]) -> Any:
    plane = _plane(tenant, user, world["payloads"])
    return plane.app.state.container.spec.coverage_owners


async def test_a_stalled_research_is_continued_by_the_sweep_once(
    world: dict[str, Any], started: list[tuple[UUID, UUID]]
) -> None:
    from account_intelligence.db.session import SessionLocal, set_system, tenant_txn
    from account_intelligence.replica.auto_resume import auto_request_id, auto_resume_pass
    from account_intelligence.runs.sweeper import mark_stalled

    tenant, user = world["a"], f"auto-{uuid4().hex[:6]}"
    owners = _owners(tenant, user, world)
    run, _ = _run(tenant, user, "RUNNING", idle_minutes=45)
    _job(tenant, run, user)
    set_system()
    now = datetime.now(UTC)
    assert run in await mark_stalled(SessionLocal, tenant_txn, now)
    done = await auto_resume_pass(owners, now)
    assert done["continued"] >= 1
    (status, reason) = _db("SELECT status, finished_reason FROM account_runs WHERE id = %s", run)[0]
    assert status in ("PARTIAL", "FAILED") and reason == "stalled"
    jobs_ = _continuations(run)
    assert len(jobs_) == 1
    job_id, job_status, requested_by, request_id = jobs_[0]
    assert (job_status, requested_by, request_id) == ("queued", user, auto_request_id(run))
    assert (tenant, UUID(str(job_id))) in started
    kinds = _kinds(tenant, user)
    assert kinds.count("research_stalled") == 1 and kinds.count("research_resumed") == 1
    assert _db(
        "SELECT count(*) FROM audit_events WHERE tenant_id = %s"
        " AND action = 'run.auto_resume_research' AND resource_id = %s",
        tenant,
        str(job_id),
    ) == [(1,)]
    # a second pass changes nothing: the continuation is one job, told once
    before = len(started)
    await auto_resume_pass(owners, now + timedelta(minutes=5))
    assert len(_continuations(run)) == 1 and len(started) == before
    assert _kinds(tenant, user).count("research_resumed") == 1


async def test_automatic_continuations_are_bounded(
    world: dict[str, Any], started: list[tuple[UUID, UUID]]
) -> None:
    from account_intelligence.db.session import SessionLocal, set_system, tenant_txn
    from account_intelligence.replica.auto_resume import (
        MAX_AUTO_RESUMES,
        auto_request_id,
        auto_resume_pass,
    )
    from account_intelligence.runs.sweeper import mark_stalled

    tenant, user = world["a"], f"bound-{uuid4().hex[:6]}"
    owners = _owners(tenant, user, world)
    assert MAX_AUTO_RESUMES == 5
    # a chain the platform already continued MAX_AUTO_RESUMES times: a -> b -> ... -> c
    prev, _ = _run(tenant, user, "PARTIAL")
    _job(tenant, prev, user, status="done")
    for _ in range(MAX_AUTO_RESUMES - 1):
        nxt, _ = _run(tenant, user, "PARTIAL")
        _job(tenant, nxt, user, status="done", resume_from=prev, request_id=auto_request_id(prev))
        prev = nxt
    c, _ = _run(tenant, user, "RUNNING", idle_minutes=45)
    _job(tenant, c, user, resume_from=prev, request_id=auto_request_id(prev))
    set_system()
    now = datetime.now(UTC)
    assert c in await mark_stalled(SessionLocal, tenant_txn, now)
    await auto_resume_pass(owners, now)
    assert _continuations(c) == []
    assert _db("SELECT status FROM account_runs WHERE id = %s", c) == [("RUNNING",)]
    assert not [
        x
        for x in started
        if x[0] == tenant
        and _db("SELECT 1 FROM research_jobs WHERE id = %s AND requested_by = %s", x[1], user)
    ]
    # the owner was told it stalled (once) and keeps the Resume button
    assert _kinds(tenant, user) == ["research_stalled"]


async def test_stops_the_owner_must_fix_and_old_stops_are_not_continued(
    world: dict[str, Any], started: list[tuple[UUID, UUID]]
) -> None:
    from account_intelligence.db.session import set_system
    from account_intelligence.replica.auto_resume import auto_resume_pass

    tenant, user = world["a"], f"fix-{uuid4().hex[:6]}"
    owners = _owners(tenant, user, world)
    refused, _ = _run(tenant, user, "PARTIAL")  # the model provider refused: a person restores it
    _db(
        "UPDATE account_runs SET finished_reason = 'model_credits_exhausted' WHERE id = %s", refused
    )
    _job(tenant, refused, user, status="done")
    old, _ = _run(tenant, user, "PARTIAL")  # lost its worker two days ago
    _db("UPDATE account_runs SET finished_at = now() - interval '2 days' WHERE id = %s", old)
    _job(tenant, old, user, status="done")
    no_job, _ = _run(tenant, user, "PARTIAL")  # a Workflow v1 run started through the API
    set_system()
    await auto_resume_pass(owners, datetime.now(UTC))
    for run in (refused, old, no_job):
        assert _continuations(run) == [], run
    assert _kinds(tenant, user) == []


async def test_a_run_the_worker_lost_is_continued_and_a_live_lease_is_respected(
    world: dict[str, Any], started: list[tuple[UUID, UUID]]
) -> None:
    from account_intelligence.db.session import SessionLocal, set_system, tenant_txn
    from account_intelligence.replica.auto_resume import auto_resume_pass
    from account_intelligence.runs.sweeper import mark_stalled

    tenant, user = world["a"], f"lost-{uuid4().hex[:6]}"
    owners = _owners(tenant, user, world)
    lost, _ = _run(tenant, user, "PARTIAL")  # ended worker_lost by the orphan sweep just now
    _job(tenant, lost, user, status="done")
    held, _ = _run(tenant, user, "RUNNING", idle_minutes=45)  # quiet, but a worker holds it
    _job(tenant, held, user)
    _db(
        "INSERT INTO run_leases (run_id, tenant_id, owner, epoch, expires_at, heartbeat_at)"
        " VALUES (%s, %s, 'worker-1', 1, now() + interval '10 minutes', now())",
        held,
        tenant,
    )
    set_system()
    now = datetime.now(UTC)
    assert held in await mark_stalled(SessionLocal, tenant_txn, now)
    await auto_resume_pass(owners, now)
    (lost_job,) = _continuations(lost)
    assert lost_job[1] == "queued" and lost_job[2] == user
    assert (tenant, UUID(str(lost_job[0]))) in started
    assert _db("SELECT status FROM account_runs WHERE id = %s", lost) == [("PARTIAL",)]
    assert _continuations(held) == []  # a live lease: the worker may still be on a long call
    assert _db("SELECT status FROM account_runs WHERE id = %s", held) == [("RUNNING",)]
    # the lease expires and nothing moved: the next pass continues it
    _db("DELETE FROM run_leases WHERE run_id = %s", held)
    await auto_resume_pass(owners, now + timedelta(minutes=5))
    assert len(_continuations(held)) == 1
    assert _db("SELECT finished_reason FROM account_runs WHERE id = %s", held) == [("stalled",)]
    assert _kinds(tenant, user).count("research_resumed") == 2


async def test_a_dropped_connection_or_a_provider_outage_that_ended_a_run_is_continued(
    world: dict[str, Any], started: list[tuple[UUID, UUID]]
) -> None:
    """F14 and the owner's decision of 2026-10-30: the last four nodes retry a blip; one that
    outlasts the retries ends the run with its class, which a fresh attempt mends. A provider
    outage (429, 5xx, timeout, open breaker) is continued the same way, like
    ``model_unavailable``. A refusal the owner must fix and a slice ceiling are not."""
    from account_intelligence.db.session import set_system
    from account_intelligence.replica.auto_resume import (
        INFRASTRUCTURE_STOPS,
        PROVIDER_OUTAGE_STOPS,
        auto_resume_pass,
    )

    tenant, user = world["a"], f"infra-{uuid4().hex[:6]}"
    owners = _owners(tenant, user, world)
    continued: dict[str, UUID] = {}
    for reason in ("OperationalError", *sorted(PROVIDER_OUTAGE_STOPS), "model_unavailable"):
        run, _ = _run(tenant, user, "PARTIAL")
        _db("UPDATE account_runs SET finished_reason = %s WHERE id = %s", reason, run)
        _job(tenant, run, user, status="done")
        continued[reason] = run
    must_not: dict[str, UUID] = {}
    for reason in (
        "slice_ceiling",
        "AuthError",
        "CreditsExhausted",
        "model_auth_failed",
        "model_credits_exhausted",
        "token_guard_stopped",
        "entity_mismatch",
    ):
        run, _ = _run(tenant, user, "PARTIAL")
        _db("UPDATE account_runs SET finished_reason = %s WHERE id = %s", reason, run)
        _job(tenant, run, user, status="done")
        must_not[reason] = run
    set_system()
    await auto_resume_pass(owners, datetime.now(UTC))
    assert {"RateLimited", "Overloaded", "RetryableProviderError"} <= PROVIDER_OUTAGE_STOPS
    assert not (PROVIDER_OUTAGE_STOPS & INFRASTRUCTURE_STOPS)
    for reason, run in continued.items():
        assert len(_continuations(run)) == 1, reason
    for reason, run in must_not.items():
        assert _continuations(run) == [], reason


async def test_a_provider_outage_is_bounded_per_chain_and_by_the_window(
    world: dict[str, Any], started: list[tuple[UUID, UUID]]
) -> None:
    """The owner's rule of 2026-10-30: the outage continuation keeps MAX_AUTO_RESUMES per chain
    and the 24 h window, exactly like ``model_unavailable``."""
    from account_intelligence.db.session import set_system
    from account_intelligence.replica.auto_resume import (
        MAX_AUTO_RESUMES,
        auto_request_id,
        auto_resume_pass,
    )

    tenant, user = world["a"], f"outage-{uuid4().hex[:6]}"
    owners = _owners(tenant, user, world)
    old, _ = _run(tenant, user, "PARTIAL")
    _db(
        "UPDATE account_runs SET finished_reason = 'RateLimited',"
        " finished_at = now() - interval '2 days' WHERE id = %s",
        old,
    )
    _job(tenant, old, user, status="done")
    prev, _ = _run(tenant, user, "PARTIAL")
    _job(tenant, prev, user, status="done")
    for _ in range(MAX_AUTO_RESUMES - 1):
        nxt, _ = _run(tenant, user, "PARTIAL")
        _job(tenant, nxt, user, status="done", resume_from=prev, request_id=auto_request_id(prev))
        prev = nxt
    last, _ = _run(tenant, user, "PARTIAL")
    _db("UPDATE account_runs SET finished_reason = 'RateLimited' WHERE id = %s", last)
    _job(tenant, last, user, status="done", resume_from=prev, request_id=auto_request_id(prev))
    set_system()
    await auto_resume_pass(owners, datetime.now(UTC))
    assert _continuations(old) == []  # two days old: the owner's to resume
    assert _continuations(last) == []  # the chain already used its continuations


async def test_every_stop_that_can_occur_has_a_plain_message() -> None:
    """D1 open question 4: token_guard_stopped, no_progress, entity_mismatch and the rest used
    to read "It stopped before finishing (reason: ...)". A slice ceiling says the owner has to
    press Resume; an automatic continuation says the platform goes on by itself."""
    from account_intelligence.replica.auto_resume import ELIGIBLE
    from account_intelligence.replica.workspace import stop_text

    generic = "It stopped before finishing"
    codes = [
        "worker_lost",
        "stalled",
        "slice_ceiling",
        "no_progress",
        "token_guard_stopped",
        "entity_mismatch",
        "invalid_setup",
        "no_pulled_data",
        "stale_pulls",
        "sense_check_budget_exceeded",
        "distill_budget_exceeded",
        "sense_check_missing",
        "sense_check_stale",
        "manifest_changed",
        "third_party_domain",
        "client_output_leak",
        "approval_timeout",
        "canceled_by_user",
        "budget_cap",
        "model_credits_exhausted",
        "model_auth_failed",
        "model_unavailable",
        "model_not_configured",
        "model_refused",
        "DollarCapReached",
        "RepeatedCallStopped",
        "gate_failed",
        "files_withheld",
    ]
    for code in (*codes, *sorted(ELIGIBLE)):
        assert not stop_text(code).startswith(generic), code
    assert "Resume" in stop_text("slice_ceiling") and "by itself" in stop_text("slice_ceiling")
    assert "continues the research by itself" in stop_text("RateLimited")
    assert "continues the research by itself" in stop_text("OperationalError")
    assert "Resume" in stop_text("no_progress") and "Resume" in stop_text("token_guard_stopped")
    assert "different company" in stop_text("entity_mismatch")
