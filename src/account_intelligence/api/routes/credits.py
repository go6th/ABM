"""Credits, Refresh from web and notifications.

* ``GET /v1/credits``: the client's monthly pool (used, held, left, when it resets).
* ``GET /v1/accounts/{domain}/refresh``: the three scopes with time and credit estimates, the
  credits left, whether users may refresh from the web, and any refresh already open.
* ``POST /v1/accounts/{domain}/refresh``: Confirm. The body repeats the credits the user saw; if
  the estimate moved, nothing is reserved (409 ``estimate_changed`` with the new figure). Short
  credits: 409 ``credits_short``; an open refresh: 409 ``refresh_in_progress``; feature off: 403.
  Otherwise the credits are reserved and the job starts; a repeated ``request_id`` returns the
  same job. Nothing leaves the workspace until this call.
* ``GET /v1/notifications`` and ``POST /v1/notifications/{id}/read``: the user's own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.routes.account_workspace import account_id_of
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import credits as CR
from account_intelligence.replica import jobs
from account_intelligence.replica.settings import client_settings

router = APIRouter(tags=["credits"])
register("GET", "/v1/credits", tenant_route("read_public", "read", step_up=False))
register("GET", "/v1/accounts/{domain}/refresh", tenant_route("read_public", "read", step_up=False))
register("POST", "/v1/accounts/{domain}/refresh", tenant_route("start_run", "run", step_up=False))
register("GET", "/v1/notifications", tenant_route("read_public", "read", step_up=False))
register(
    "POST",
    "/v1/notifications/{notification_id}/read",
    tenant_route("read_public", "none", step_up=False),
)

OPTIONS = {
    "jobs": ("Job postings only", "New and closed roles in your lanes"),
    "people": ("People and teams", "Senior people changes and team signals"),
    "full": ("Full refresh", "News, jobs, technology, people and speaking"),
}


@router.get("/credits", response_model=C.Credits)
async def credits_now(ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The caller's client pool for this month."""
    async with tenant_txn(ident.tenant_id) as s:
        return await CR.pool(s, datetime.now(UTC))


async def _refresh_view(s: Any, account_id: UUID, now: datetime) -> dict[str, Any]:
    settings = await client_settings(s)
    acct = (
        await s.execute(
            text("SELECT domain, coalesce(name, domain) AS name FROM accounts WHERE id = :a"),
            {"a": account_id},
        )
    ).one()
    last = await s.scalar(
        text(
            "SELECT max(f) FROM (SELECT finished_at AS f FROM account_runs WHERE account_id = :a"
            " AND status IN ('COMPLETED', 'PARTIAL') UNION ALL SELECT finished_at FROM"
            " research_jobs"
            " WHERE account_id = :a AND status = 'done') x"
        ),
        {"a": account_id},
    )
    pool = await CR.pool(s, now)
    options = []
    for scope, (name, desc) in OPTIONS.items():
        est = await CR.estimate(s, account_id, scope)
        options.append(
            {
                "scope": scope,
                "name": name,
                "description": desc,
                "minutes": est.minutes,
                "credits": est.credits,
                "affordable": est.credits <= pool["left"],
            }
        )
    job = await CR.open_job(s, account_id)
    return {
        "domain": acct.domain,
        "name": acct.name,
        "refreshed_days_ago": None if last is None else max(0, (now - last).days),
        "allowed": settings.web_refresh_allowed,
        "credits": pool,
        "cap_per_pull": await CR.credit_cap_per_pull(s),
        "options": options,
        "open_job": None
        if job is None
        else {"id": str(job.id), "kind": job.kind, "scope": job.scope, "status": job.status},
    }


@router.get("/accounts/{domain}/refresh", response_model=C.RefreshOptions)
async def refresh_options(domain: str, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """What a web refresh of this account would cost, before anything is confirmed."""
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        return await _refresh_view(s, account_id, datetime.now(UTC))


class RefreshBody(BaseModel):
    """Confirm a refresh. ``credits`` is the estimate the user saw and agreed to."""

    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    scope: Literal["jobs", "people", "full"]
    credits: int = Field(ge=1, le=100_000)
    from_turn_id: UUID | None = None


@router.post("/accounts/{domain}/refresh", response_model=C.RefreshStarted, status_code=201)
async def confirm_refresh(
    domain: str, body: RefreshBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Reserve the credits the user saw and start the refresh."""
    now = datetime.now(UTC)
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        if not (await client_settings(s)).web_refresh_allowed:
            raise APIProblem(
                403,
                "web_refresh_not_allowed",
                "Web refresh is turned off",
                "Your administrator has turned off web refreshes for your workspace.",
            )
        est = await CR.estimate(s, account_id, body.scope)
        if est.credits != body.credits:
            raise APIProblem(
                409,
                "estimate_changed",
                "The estimate changed",
                f"This refresh now needs about {est.credits} credits. Check it and confirm again.",
                extra={"credits": est.credits},
            )
        try:
            job_id, status = await CR.reserve(
                s,
                account_id=account_id,
                kind="web_refresh",
                scope=body.scope,
                credits=est.credits,
                actor=ident.user_id,
                now=now,
                request_id=body.request_id,
                from_turn_id=body.from_turn_id,
            )
        except CR.JobOpen:
            raise APIProblem(
                409,
                "refresh_in_progress",
                "A refresh is already running",
                "This account is already being refreshed. You will be notified when it finishes.",
            ) from None
        except CR.CreditsShort as short:
            raise APIProblem(
                409,
                "credits_short",
                "Not enough credits",
                f"This refresh needs about {short.needed} credits and {short.left} are left this"
                " month.",
                extra={"needed": short.needed, "left": short.left},
            ) from None
        await audit_action(
            s,
            request,
            audit_class="run",
            action="confirm_refresh",
            resource_type="research_job",
            resource_id=job_id,
        )
    if status == "queued":
        await jobs.run_or_enqueue(ident.tenant_id, job_id)
    return {"job_id": str(job_id), "status": status, "credits": est.credits}


@router.get("/notifications", response_model=C.Notifications)
async def notifications(ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The caller's latest notifications, unread first."""
    async with tenant_txn(ident.tenant_id) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT n.id, n.kind, n.created_at, n.read_at, a.domain, coalesce(a.name,"
                    " a.domain) AS name,"
                    " j.scope, j.credits_used, j.failure_reason"
                    " FROM notifications n LEFT JOIN accounts a ON a.id = n.account_id"
                    " LEFT JOIN research_jobs j ON j.id = n.job_id"
                    " WHERE n.user_id = :u ORDER BY (n.read_at IS NULL) DESC, n.created_at DESC"
                    " LIMIT 20"
                ),
                {"u": ident.user_id},
            )
        ).all()
    return {
        "notifications": [
            {
                "id": str(r.id),
                "kind": r.kind,
                "created_at": r.created_at.isoformat(),
                "read": r.read_at is not None,
                "account_domain": r.domain,
                "account_name": r.name,
                "scope": r.scope,
                "credits_used": r.credits_used,
                "reason": r.failure_reason,
            }
            for r in rows
        ],
        "unread": sum(1 for r in rows if r.read_at is None),
    }


@router.post("/notifications/{notification_id}/read", response_model=C.NotificationRead)
async def notification_read(
    notification_id: UUID, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Mark one of the caller's notifications read."""
    async with tenant_txn(ident.tenant_id) as s:
        done = await s.scalar(
            text(
                "UPDATE notifications SET read_at = coalesce(read_at, now()) WHERE id = :n AND"
                " user_id = :u"
                " RETURNING id"
            ),
            {"n": notification_id, "u": ident.user_id},
        )
    if done is None:
        raise HTTPException(404, "not found")
    return {"read": True}
