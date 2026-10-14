"""Running a confirmed research job: claim it, run the research engine within its reserved
credits, settle what it really used, and tell the user.

The engine is a port. ``HermeticEngine`` (development and tests) makes no outside call: it records
a completed refresh run with no new findings and reports spend as a fixed share of the reserve, so
credits, statuses and notifications can be exercised end to end without paying anyone.
``SkillEngine`` is the production engine; until the research skill executor exists it refuses
with a plain reason, the job fails and its credits are released untouched.

``run_job`` is idempotent: a job that is not ``queued`` is left as it is, so a redelivered task
never runs or charges twice.

GUARANTEE(test_a_redelivered_job_runs_and_charges_once)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import credits as CR

log = logging.getLogger(__name__)
GRADING_PLACEHOLDER = "0" * 64


@dataclass(frozen=True)
class Job:
    """A claimed job, as the engine sees it."""

    id: UUID
    tenant_id: UUID
    account_id: UUID
    kind: str
    scope: str
    credits_reserved: int
    requested_by: str
    topic: str | None = None


@dataclass(frozen=True)
class EngineResult:
    """What the engine did: the run it recorded and what it really spent."""

    run_id: UUID | None
    spent_usd: Decimal


class EngineNotConfigured(Exception):
    """The research engine cannot run here; the reason is shown to the user."""


class ResearchEngine(Protocol):
    """Runs one research job within ``credits_reserved``."""

    async def run(self, job: Job) -> EngineResult:
        """Research the account for the job's scope and report the real spend."""
        ...


class HermeticEngine:
    """Development and tests: no outside call. Records a completed run and spends a fixed share
    of the reserve (``share``), so the pool moves the way a real refresh would."""

    def __init__(self, share: Decimal = Decimal("0.8")) -> None:
        self.share = share

    async def run(self, job: Job) -> EngineResult:
        now = datetime.now(UTC)
        async with tenant_txn(job.tenant_id) as s:
            run_id = (
                await s.execute(
                    text(
                        "INSERT INTO account_runs (tenant_id, account_id, status,"
                        " grading_config_hash, created_at,"
                        " finished_at, finished_reason, input_mode, run_mode, cap_required,"
                        " graph_version,"
                        " budget_json, reserved_credits, idempotency_key, run_input)"
                        " VALUES (:t, :a, 'COMPLETED', :g, :now, :now, 'completed', 'company',"
                        " 'full', false,"
                        " 'hermetic-research', '{}', :c, :k, CAST(:inp AS jsonb)) RETURNING id"
                    ),
                    {
                        "t": job.tenant_id,
                        "a": job.account_id,
                        "g": GRADING_PLACEHOLDER,
                        "now": now,
                        "c": job.credits_reserved,
                        "k": f"job-{job.id}",
                        "inp": json.dumps(
                            {"scope": job.scope, "job_id": str(job.id), "topic": job.topic}
                        ),
                    },
                )
            ).scalar_one()
        spent = Decimal(job.credits_reserved) * self.share * CR.rates().credit_value_usd
        return EngineResult(UUID(str(run_id)), spent)


class SkillEngine:
    """Production: the research skill executor. Not built yet (the skill package is missing)."""

    async def run(self, job: Job) -> EngineResult:
        raise EngineNotConfigured("The research engine is not set up yet. No credits were used.")


def engine_from_env(app_env: str) -> ResearchEngine:
    """``RESEARCH_ENGINE`` chooses; development and tests default to the hermetic engine."""
    choice = os.environ.get("RESEARCH_ENGINE") or (
        "hermetic" if app_env in ("dev", "test") else "skill"
    )
    if choice == "hermetic":
        if app_env not in ("dev", "test"):
            raise RuntimeError("the hermetic research engine is for development and tests only")
        return HermeticEngine()
    return SkillEngine()


NOTE_KIND = {
    "web_refresh": "refresh_finished",
    "scheduled_refresh": "refresh_finished",
    "new_research": "research_finished",
    "file_analysis": "research_finished",
}


async def claim(tenant_id: UUID, job_id: UUID, now: datetime) -> Job | None:
    """Move a queued job to running; None when it is not queued (already taken or ended)."""
    async with tenant_txn(tenant_id) as s:
        row = (
            await s.execute(
                text(
                    "UPDATE research_jobs SET status = 'running', started_at = :now"
                    " WHERE id = :j AND status = 'queued'"
                    " RETURNING id, account_id, kind, scope, credits_estimated, requested_by, topic"
                ),
                {"j": job_id, "now": now},
            )
        ).first()
    if row is None:
        return None
    return Job(
        UUID(str(row.id)),
        tenant_id,
        UUID(str(row.account_id)),
        row.kind,
        row.scope,
        int(row.credits_estimated),
        str(row.requested_by),
        row.topic,
    )


async def run_job(tenant_id: UUID, job_id: UUID, engine: ResearchEngine) -> str:
    """Claim, run, settle, notify. Returns the job's end status (or 'skipped')."""
    job = await claim(tenant_id, job_id, datetime.now(UTC))
    if job is None:
        return "skipped"
    status, reason, spent, run_id = "done", None, Decimal(0), None
    try:
        result = await engine.run(job)
        spent, run_id = result.spent_usd, result.run_id
    except EngineNotConfigured as err:
        status, reason = "failed", str(err)
    except Exception:
        log.exception("research_job_failed", extra={"job_id": str(job_id)})
        status, reason = (
            "failed",
            "The research did not finish. No credits beyond what it used were charged.",
        )
    now = datetime.now(UTC)
    async with tenant_txn(tenant_id) as s:
        await CR.settle(s, job.id, spent_usd=spent, now=now, status=status, reason=reason)
        if run_id is not None:
            await s.execute(
                text("UPDATE research_jobs SET run_id = :r WHERE id = :j"),
                {"r": run_id, "j": job.id},
            )
        if job.requested_by != "scheduler":
            await s.execute(
                text(
                    "INSERT INTO notifications (user_id, kind, account_id, job_id) VALUES (:u, :k,"
                    " :a, :j)"
                ),
                {
                    "u": job.requested_by,
                    "a": job.account_id,
                    "j": job.id,
                    "k": NOTE_KIND[job.kind] if status == "done" else "research_failed",
                },
            )
        promoted = await CR.promote_waiting(s, now)
    for queued in promoted:
        await run_or_enqueue(tenant_id, queued)
    return status


# The scheduler that starts jobs (set at application start; tests use run_job directly).
_scheduler: Any = None
_engine: ResearchEngine | None = None


def configure(scheduler: Any, engine: ResearchEngine) -> None:
    """Wire the task scheduler and the engine (called once at startup)."""
    global _scheduler, _engine
    _scheduler, _engine = scheduler, engine


async def run_or_enqueue(tenant_id: UUID, job_id: UUID) -> None:
    """Hand a queued job to the worker (or run it here when no worker queue is wired)."""
    if _scheduler is not None and hasattr(_scheduler, "enqueue_research_job"):
        await _scheduler.enqueue_research_job(tenant_id, job_id)
    elif _engine is not None:
        await run_job(tenant_id, job_id, _engine)
