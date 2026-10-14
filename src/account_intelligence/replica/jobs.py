"""Running a confirmed research job: claim it, start the research within its reserved credits,
and settle what it really used.

The engine is a port, chosen by ``RESEARCH_ENGINE``:

* ``graph`` (staging and production default): the bridge to the existing run machinery. It
  creates an ``account_runs`` run through the run lifecycle for the job (``CreditJob(job_id)``:
  the job's reserve is the run's credits and budget, no second reservation), records the run's
  mode and settings pin, and hands slice 0 to the worker. The run executes on the ABM agent graph
  (``agents.production.wiring.uses_abm``: a job's run always does) with the job's scope: the
  rows of ``config/research_scopes.yaml`` (jobs, people, full, topic; a topic refresh searches
  with the user's words). The job is settled once when the run ends (``runs.lifecycle.
  settle_once``: real spend through the rate table, never more than reserved, nothing when the
  platform failed) and the person who asked is told then. The ABM research skill executor
  (BUILD_PROMPT section 4) takes over when the skill package is installed and the executor is
  built; until then this graph is the engine (``engine_state`` says which, for readiness).
* ``hermetic`` (development and tests default): no outside call. It records a completed run with
  no new findings and spends a fixed share of the reserve, so credits, statuses and
  notifications can be exercised without paying anyone.
* ``skill``: refuses with a plain reason until the skill executor exists (nothing is charged).

``run_job`` is idempotent: a job that is not ``queued`` is left as it is, so a redelivered task
never runs or charges twice. A failure before the run exists releases the reserve untouched.

GUARANTEE(test_a_redelivered_job_runs_and_charges_once)
GUARANTEE(test_a_confirmed_refresh_runs_on_the_lifecycle_and_settles_when_the_run_ends)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any, Protocol
from uuid import UUID

import yaml
from sqlalchemy import text

from account_intelligence.config import CONFIG_DIR, SKILL_PACKAGE_DIR
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import credits as CR
from account_intelligence.replica.notifications import notify
from account_intelligence.runs.lifecycle import record_hermetic_run
from obs.logging import logger

log = logger(__name__)
GRADING_PLACEHOLDER = "0" * 64
SCOPES_FILE = CONFIG_DIR / "research_scopes.yaml"
SKILL_PACKAGE = SKILL_PACKAGE_DIR / "SKILL.md"


@lru_cache(maxsize=1)
def scope_rows() -> dict[str, tuple[str, ...]]:
    """Each job scope's coverage rows (empty: every row), validated against the plan once."""
    from agents.config import skill_config

    raw = yaml.safe_load(SCOPES_FILE.read_text())["scopes"]
    plan = set(skill_config().plan)
    out = {k: tuple(v or ()) for k, v in raw.items()}
    for scope in CR.SCOPES:
        if scope not in out:
            raise ValueError(f"research_scopes.yaml needs the scope {scope}")
        unknown = set(out[scope]) - plan
        if unknown:
            raise ValueError(f"research_scopes.yaml: {scope} names unknown rows {sorted(unknown)}")
    return out


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
    # the research and analysis models the user confirmed (None: the owner's current models)
    models: dict[str, Any] | None = None
    # the stopped run this job continues (its finished pulls and replies are reused at no cost)
    resume_from: UUID | None = None


@dataclass(frozen=True)
class EngineResult:
    """What the engine did: the run it recorded and what it spent so far. ``pending`` means the
    run is still going; it settles the job itself when it ends."""

    run_id: UUID | None
    spent_usd: Decimal
    pending: bool = False


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
        """Record a finished run with no findings and spend a fixed share of the reserve."""
        now = datetime.now(UTC)
        async with tenant_txn(job.tenant_id) as s:
            run_id = await record_hermetic_run(
                s,
                job.tenant_id,
                job.account_id,
                reserved_credits=job.credits_reserved,
                idempotency_key=f"job-{job.id}",
                run_input={"scope": job.scope, "job_id": str(job.id), "topic": job.topic},
                grading_config_hash=GRADING_PLACEHOLDER,
                now=now,
            )
        spent = Decimal(job.credits_reserved) * self.share * CR.rates().credit_value_usd
        return EngineResult(run_id, spent)


class GraphEngine:
    """The bridge to the run machinery (see the module docstring)."""

    def __init__(self, container: Any) -> None:
        self.container = container

    async def run(self, job: Job) -> EngineResult:
        """Create the job's run through the lifecycle and hand slice 0 to the worker."""
        from account_intelligence.grading.config import load_grading_config
        from account_intelligence.runs import lifecycle
        from account_intelligence.tracker.adapters import LedgerCallAdapter, RunInputAdapter
        from account_intelligence.tracker.repo import CoverageRepo
        from account_intelligence.tracker.service import TrackerService
        from agents.adapters import run_mode_columns
        from agents.config import skill_config
        from agents.modes import RunMode, plan_run
        from metering import events as usage
        from tenancy import settings

        now = datetime.now(UTC)
        rows = scope_rows().get(job.scope, ())
        async with tenant_txn(job.tenant_id) as s:
            domain = await s.scalar(
                text("SELECT domain FROM accounts WHERE id = :a"), {"a": job.account_id}
            )
            if domain is None:
                raise EngineNotConfigured("The account is no longer in your workspace.")
            try:
                current = await settings.ensure_saved(s, actor=job.requested_by, now=now)
            except settings.SettingsInvalid:
                raise EngineNotConfigured(
                    "Your workspace settings need attention: an admin must save them again."
                    " No credits were used."
                ) from None
            rule = settings.ExclusionFilter.of(current.settings)
            if rule.excludes_company(domain):
                raise EngineNotConfigured("This company is on your exclusion list.")
            lanes, _, _ = settings.platform_options()
            run_input: dict[str, Any] = {
                "mode": "full",
                "job_id": str(job.id),
                "scope": job.scope,
                "lanes_off": sorted(set(lanes) - set(current.settings.platform_lanes)),
                "pursuing_client": current.settings.pursuing_client or None,
            }
            if rows:
                run_input["scope_rows"] = list(rows)
            if job.topic:
                run_input["topic"] = job.topic
            if job.models:
                run_input["models"] = dict(job.models)
            if job.resume_from is not None:
                run_input["resume_from"] = str(job.resume_from)
            if current.settings.people_scope.regions:
                run_input["people_regions"] = list(current.settings.people_scope.regions)
            if rule.people or rule.companies:
                run_input.update(rule.run_input())
            tracker = TrackerService(
                CoverageRepo(s),
                LedgerCallAdapter(s, load_grading_config().category_by_operation),
                RunInputAdapter(s),
                owners=self.container.spec.coverage_owners,
            )
            created = await lifecycle.create_run(
                s,
                job.tenant_id,
                lifecycle.RunRequest(str(domain), "standard", f"job-{job.id}", run_input),
                job_limits(self.container.limits, rows),
                tracker,
                lifecycle.CreditJob(
                    kind=job.kind, scope=job.scope, actor=job.requested_by, job_id=job.id
                ),
            )
            if not created.replayed:
                plan = plan_run(
                    RunMode.FULL,
                    parent_run_id=None,
                    parent_mode=None,
                    parent_config_version=None,
                    requested_config_version=None,
                    current_config_version=skill_config().config_version,
                )
                await run_mode_columns(s, created.run_id, plan)
                await settings.pin_run(s, created.run_id, current.version)
                await usage.record(s, "run_started", created.run_id, now)
        if not created.replayed:
            await self.container.scheduler.enqueue_slice(job.tenant_id, created.run_id, 0)
        return EngineResult(created.run_id, Decimal(0), pending=True)


def job_limits(limits: Any, rows: tuple[str, ...]) -> Any:
    """The run limits of a job: the agent graph searches each row in scope with every
    configured phrasing, plus follow-ups and page reads, so its call cap is sized from its own
    plan (rows x phrasings x 2), never below the configured cap. Money stays capped by the
    budget, which is the job's credits (FIX_LOG D12)."""
    from dataclasses import replace

    from agents.config import skill_config

    cfg = skill_config()
    planned = len(rows or tuple(cfg.plan)) * max(1, len(cfg.phrasings)) * 2
    return replace(limits, tool_call_cap=max(int(limits.tool_call_cap), planned))


class SkillEngine:
    """The ABM research skill executor (BUILD_PROMPT section 4). Not built: it is designed
    against the owner's skill package, which is not installed."""

    async def run(self, job: Job) -> EngineResult:
        """Refuse with a plain reason; nothing is charged."""
        raise EngineNotConfigured("The research engine is not set up yet. No credits were used.")


ENGINES = ("graph", "hermetic", "skill")


def engine_choice(app_env: str) -> str:
    """``RESEARCH_ENGINE``, else ``hermetic`` in development and tests and ``graph`` elsewhere."""
    choice = os.environ.get("RESEARCH_ENGINE") or (
        "hermetic" if app_env in ("dev", "test") else "graph"
    )
    if choice not in ENGINES:
        raise RuntimeError(f"RESEARCH_ENGINE must be one of {ENGINES}")
    if choice == "hermetic" and app_env not in ("dev", "test"):
        raise RuntimeError("the hermetic research engine is for development and tests only")
    return choice


def engine_for(container: Any) -> ResearchEngine:
    """The engine for this process (see ``engine_choice``)."""
    choice = engine_choice(container.env)
    if choice == "hermetic":
        return HermeticEngine()
    if choice == "graph":
        return GraphEngine(container)
    return SkillEngine()


def engine_state(app_env: str) -> str:
    """Readiness words for the research engine, naming what is missing."""
    choice = engine_choice(app_env)
    package = SKILL_PACKAGE.is_file()
    if choice == "graph":
        missing = "installed, executor not built" if package else "not installed"
        return f"abm_graph (skill package {missing})"
    if choice == "skill":
        return "skill (not built: no research runs until the executor exists)"
    return "hermetic (no outside call)"


NOTE_KIND = {
    "web_refresh": "refresh_finished",
    "scheduled_refresh": "refresh_finished",
    "new_research": "research_finished",
    "file_analysis": "research_finished",
}


async def claim(tenant_id: UUID, job_id: UUID, now: datetime) -> Job | None:
    """Move a queued job to running; None when it is not queued (already taken or ended)."""
    async with tenant_txn(tenant_id) as s:
        row = await CR.claim(s, job_id, now)
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
        dict(row.models) if row.models else None,
        UUID(str(row.resume_from)) if row.resume_from else None,
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
        if result.pending:
            return "started"  # the run settles the job and tells the user when it ends
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
            await CR.link_run(s, job.id, run_id)
        if job.requested_by != "scheduler":
            await notify(
                s,
                user_id=job.requested_by,
                kind=NOTE_KIND[job.kind] if status == "done" else "research_failed",
                account_id=job.account_id,
                job_id=job.id,
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
