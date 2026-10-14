"""New research on a company, on the client's credit pool (screens 6 and 9: "Research a new
company"): the estimate first, then Confirm reserves the credits and starts the research.

* ``GET /v1/research/estimate``: credits and time for a new company (the rate table, never above
  the platform's credit cap per pull), the credits left this month, and the caller's account cap.
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
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.routes.model_choice import refused
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import credits as CR
from account_intelligence.replica import jobs
from account_intelligence.replica import models as M
from account_intelligence.replica.settings import accounts_used, client_settings
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
    credits: int = Field(ge=1, le=100_000)
    # the models chosen on the screen (owner decision 2026-10-14); None: the user's current ones
    research_model: str | None = Field(default=None, min_length=1, max_length=64)
    analysis_model: str | None = Field(default=None, min_length=1, max_length=64)


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
                s, ident.user_id, research=body.research_model, analysis=body.analysis_model
            )
        except M.ModelRefused as error:
            raise refused(error) from None
        if est.credits != body.credits:
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
