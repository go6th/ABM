"""New research on a company, on the client's credit pool (screens 6 and 9: "Research a new
company"): the estimate first, then Confirm reserves the credits and starts the research.

* ``GET /v1/research/estimate``: credits and time for a new company (the rate table, never above
  the platform's credit cap per pull), the credits left this month, and the caller's account cap.
* ``POST /v1/research/resume``: Resume research (owner decision 2026-10-14): continue a
  stopped or stalled run, reusing every finished pull and model reply at no cost; "Resuming: N of
  M steps already done".
* ``POST /v1/research/{job_id}/stop``: Stop a research that is waiting, queued or running. A run
  already carrying it out ends CANCELED (unset rows are swept and the spend so far is settled, as
  the run's own Stop does); a job no run has started yet ends canceled with its credits released.
* ``POST /v1/research``: Confirm. The body repeats the credits the user saw; if the estimate
  moved, nothing is reserved (409 ``estimate_changed`` with the new figure). The account is added
  for the caller (it counts against their account cap: 409 ``account_cap_reached``), the credits
  are reserved (409 ``credits_short`` when the pool cannot cover them) and the research starts as
  a ``new_research`` job (``replica.jobs``). A repeated ``request_id`` returns the first job.
  Nothing is searched until this call.

GUARANTEE(test_new_research_shows_the_estimate_and_starts_only_on_confirm)
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.ratelimit import limit_user
from account_intelligence.api.routes.limits import limit_reached
from account_intelligence.api.routes.model_choice import refused
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import credits as CR
from account_intelligence.replica import jobs
from account_intelligence.replica import limits as LIM
from account_intelligence.replica import models as M
from account_intelligence.replica.settings import accounts_used, client_settings
from account_intelligence.replica.usage_estimate import estimate_usage
from account_intelligence.replica.workspace import PLANNED
from account_intelligence.runs import lifecycle

router = APIRouter(prefix="/research", tags=["research"])
register("GET", "/v1/research/estimate", tenant_route("read_public", "read", step_up=False))
register("POST", "/v1/research", tenant_route("start_run", "run", step_up=False))

DOMAIN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$"


async def _estimate(s: Any, user_id: str, now: datetime) -> dict[str, Any]:
    est = await CR.estimate(s, None, "new_research")
    pool = await CR.pool(s, now)
    settings = await client_settings(s)
    used = await accounts_used(s, user_id)
    return {
        "credits": est.credits,
        "minutes": est.minutes,
        "pool": pool,
        "enough": est.credits <= pool["left"],
        "accounts_used": used,
        "account_cap": settings.account_cap_per_user,
        "usage": (await estimate_usage(s, "new_research")).as_json(),
        "file_usage": (await estimate_usage(s, "file")).as_json(),
        "file_public_usage": (await estimate_usage(s, "file_public")).as_json(),
    }


@router.get("/estimate", response_model=C.ResearchEstimate)
async def research_estimate(ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """What researching a new company would cost, before anything is confirmed."""
    async with tenant_txn(ident.tenant_id) as s:
        return await _estimate(s, ident.user_id, datetime.now(UTC))


class ResearchBody(BaseModel):
    """Confirm new research. ``credits`` is the estimate the user saw and agreed to."""

    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    company_name: str = Field(min_length=1, max_length=200)
    domain: str = Field(pattern=DOMAIN, max_length=253)
    # the credit estimate the screen was given; users no longer see credits (owner decision
    # 2026-10-14), so it is optional and only checked when sent
    credits: int | None = Field(default=None, ge=1, le=100_000)
    # the models chosen on the screen (owner decision 2026-10-14); None: the user's current ones
    research_model: str | None = Field(default=None, min_length=1, max_length=64)
    analysis_model: str | None = Field(default=None, min_length=1, max_length=64)
    # the effort beside each model for this run (medium or high; owner decision 2026-10-20)
    research_effort: str | None = Field(default=None, min_length=1, max_length=16)
    analysis_effort: str | None = Field(default=None, min_length=1, max_length=16)


@router.post("", response_model=C.ResearchStarted, status_code=201)
async def start_research(
    body: ResearchBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Add the company for the caller, reserve the credits they saw and start the research."""
    from tenancy import settings as tenant_settings

    now = datetime.now(UTC)
    async with tenant_txn(ident.tenant_id) as s:
        try:
            current = await tenant_settings.ensure_saved(s, actor=ident.user_id, now=now)
        except tenant_settings.SettingsInvalid:
            raise APIProblem(
                409,
                "settings_invalid",
                "Settings need attention",
                "An admin must save the workspace settings again.",
            ) from None
        if tenant_settings.ExclusionFilter.of(current.settings).excludes_company(body.domain):
            raise APIProblem(
                409,
                "excluded_company",
                "Excluded company",
                "This company is on your exclusion list.",
            )
        est = await CR.estimate(s, None, "new_research")
        try:
            models = await M.resolve(
                s,
                ident.user_id,
                research=body.research_model,
                analysis=body.analysis_model,
                research_effort=body.research_effort,
                analysis_effort=body.analysis_effort,
            )
        except M.ModelRefused as error:
            raise refused(error) from None
        if body.credits is not None and est.credits != body.credits:
            raise APIProblem(
                409,
                "estimate_changed",
                "The estimate changed",
                f"This research now needs about {est.credits} credits. Check it and confirm again.",
                extra={"credits": est.credits},
            )
        account_id, _ = await lifecycle.add_account(
            s, ident.tenant_id, body.domain, added_by=ident.user_id
        )
        await lifecycle.name_account(s, account_id, body.company_name)
        try:
            if not await CR.has_request(s, body.request_id):  # a retry counts nothing
                await LIM.take(s, ident.user_id, "researches", now)
        except LIM.LimitReached as reached:
            raise limit_reached(reached) from None
        try:
            job_id, status = await CR.reserve(
                s,
                account_id=account_id,
                kind="new_research",
                scope="full",
                credits=est.credits,
                actor=ident.user_id,
                now=now,
                request_id=body.request_id,
                models=models.as_json(),
                enforce_pool=False,
            )
        except CR.JobOpen:
            raise APIProblem(
                409,
                "research_in_progress",
                "Research is already running",
                "This account is already being researched. You will be notified when it finishes.",
            ) from None
        except CR.CreditsShort as short:
            raise APIProblem(
                409,
                "credits_short",
                "Not enough credits",
                f"This research needs about {short.needed} credits and {short.left} are left"
                " this month.",
                extra={"needed": short.needed, "left": short.left},
            ) from None
        await audit_action(
            s,
            request,
            audit_class="run",
            action="start_new_research",
            resource_type="research_job",
            resource_id=job_id,
        )
    if status == "queued":
        await jobs.run_or_enqueue(ident.tenant_id, job_id)
    return {
        "job_id": str(job_id),
        "status": status,
        "credits": est.credits,
        "domain": body.domain,
    }


register("POST", "/v1/research/resume", tenant_route("start_run", "run", step_up=False))


class ResumeBody(BaseModel):
    """Resume research: the stopped (or stalled) run to continue. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    request_id: UUID


@router.post("/resume", response_model=C.ResearchResumed, status_code=201)
async def resume_research(
    body: ResumeBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Continue a stopped research (owner decision 2026-10-14, Phase 4B section 4): a new run on
    the same account, scope, topic and models that reuses every finished pull and model reply of
    the stopped run at no cost (``run_input.resume_from``), so only the missing steps are paid
    for. A stalled run is ended PARTIAL first, with everything it holds. It continues the research
    already counted: it uses none of the user's researches. 409 when the run is still moving or
    finished whole."""
    await limit_user(ident, "research_resume")
    from account_intelligence.replica import auto_resume

    now = datetime.now(UTC)
    owners = request.app.state.container.spec.coverage_owners
    async with tenant_txn(ident.tenant_id) as s:
        domain = await s.scalar(
            text(
                "SELECT a.domain FROM account_runs r JOIN accounts a ON a.id = r.account_id"
                " WHERE r.id = :r"
            ),
            {"r": body.run_id},
        )
        if domain is None:
            raise APIProblem(404, "not_found", "Not found", "This research was not found.")
        try:
            out = await auto_resume.continue_run(
                s,
                run_id=body.run_id,
                actor=ident.user_id,
                request_id=body.request_id,
                coverage_owners=owners,
                now=now,
            )
        except auto_resume.NothingToContinue as why:
            raise APIProblem(409, why.code, why.title, why.text) from None
        except CR.JobOpen:
            raise APIProblem(
                409,
                "research_in_progress",
                "Research is already running",
                "This account is already being researched. You will be notified when it finishes.",
            ) from None
        if out.created:
            await audit_action(
                s,
                request,
                audit_class="run",
                action="resume_research",
                resource_type="research_job",
                resource_id=out.job_id,
            )
        answer = await _resumed(s, out.job_id, body.run_id, str(domain))
    if out.created and out.status == "queued":
        await jobs.run_or_enqueue(ident.tenant_id, out.job_id)
    return answer


async def _resumed(s: Any, job_id: Any, run_id: UUID, domain: str) -> dict[str, Any]:
    """The resume answer: the job, and how many of the stopped run's steps are already done."""
    row = (
        await s.execute(
            text(
                # the steps in the research's scope (replica.workspace.PLANNED)
                "SELECT count(*) AS total, count(*) FILTER (WHERE c.status IN ('done', 'none'))"
                f" AS done FROM coverage_rows c WHERE c.run_id = :r AND {PLANNED}"
            ),
            {"r": run_id},
        )
    ).one()
    status = await s.scalar(text("SELECT status FROM research_jobs WHERE id = :j"), {"j": job_id})
    return {
        "job_id": str(job_id),
        "status": str(status),
        "resumes_run_id": str(run_id),
        "domain": domain,
        "steps_done": int(row.done),
        "steps_total": int(row.total),
    }


register("POST", "/v1/research/{job_id}/stop", tenant_route("start_run", "run", step_up=False))


@router.post("/{job_id}/stop", response_model=C.ResearchStopped)
async def stop_research(
    job_id: UUID, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Stop the research (see the module docstring); 409 when it has already ended."""
    from account_intelligence.api.routes.runs import _tracker_for
    from account_intelligence.workflow.transitions import RunStatus

    now = datetime.now(UTC)
    container = request.app.state.container
    async with tenant_txn(ident.tenant_id) as s:
        job = (
            await s.execute(
                text("SELECT id, status, run_id FROM research_jobs WHERE id = :j FOR UPDATE"),
                {"j": job_id},
            )
        ).first()
        if job is None:
            raise APIProblem(404, "not_found", "Not found", "This research was not found.")
        if job.status not in ("waiting_credit_limit", "queued", "running"):
            raise APIProblem(
                409, "research_ended", "Research has ended", "This research has already ended."
            )
        if job.run_id is not None:
            try:
                await lifecycle.finish(
                    s,
                    await _tracker_for(container, s),
                    UUID(str(job.run_id)),
                    RunStatus.CANCELED,
                    reason="canceled_by_user",
                )
            except ValueError:
                raise APIProblem(
                    409,
                    "research_not_stoppable",
                    "Research cannot be stopped now",
                    "This research cannot be stopped from where it is. Try again in a moment.",
                ) from None
        # a job no run carries yet (or one its run's settlement left open) ends here
        await CR.settle(
            s, job_id, spent_usd=Decimal(0), now=now, status="canceled", reason="Stopped by you."
        )
        await audit_action(
            s,
            request,
            audit_class="run",
            action="stop_research",
            resource_type="research_job",
            resource_id=job_id,
        )
        status = await s.scalar(
            text("SELECT status FROM research_jobs WHERE id = :j"), {"j": job_id}
        )
    return {"job_id": str(job_id), "status": str(status)}
