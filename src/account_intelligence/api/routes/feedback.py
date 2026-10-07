"""Pilot feedback and seller usefulness (``/v1/feedback``, ``/v1/usefulness``; PL1, D-68).

* ``POST /v1/feedback`` {domain, run_id?, table?, row_id?, verdict, reason, note?}: one verdict
  on an account or on one row of a run's table (every role).
* ``GET /v1/feedback?domain=``: current verdicts (admins).
* ``POST /v1/usefulness`` {kind, subject_id, via}: a first-call question marked or copied, or a
  Confirmed fact opened or copied into a note (every role; identifiers only).
* ``GET /v1/usefulness/metric``: the tenant's usefulness metric with its base (admins).
* ``GET /operator/v1/pilot/aggregate``: per-tenant counts for 6th operators (no content).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.operator import StaffIdentity, require_staff
from account_intelligence.api.policy import OperatorRole, operator_route, register, tenant_route
from account_intelligence.api.routes.accounts import exclusion_rule, resolve_run
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn, untenanted_session
from pilot import feedback, metric, usefulness

router = APIRouter(tags=["feedback"])
operator_router = APIRouter(prefix="/operator/v1/pilot", tags=["operator"])
register("POST", "/v1/feedback", tenant_route("give_feedback", "none"))
register("GET", "/v1/feedback", tenant_route("read_usage", "read"))
register("POST", "/v1/usefulness", tenant_route("give_feedback", "none"))
register("GET", "/v1/usefulness/metric", tenant_route("read_usage", "read"))
_OPS: frozenset[OperatorRole] = frozenset({"operator"})
register("GET", "/operator/v1/pilot/aggregate", operator_route("read", tenant="none", roles=_OPS))


class FeedbackBody(BaseModel):
    """A verdict on an account (no row) or on one row of a run's table. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    domain: str = Field(max_length=253)
    run_id: UUID | None = None
    table: Literal["people", "jobs", "tech", "facts", "coverage"] | None = None
    row_id: str | None = Field(default=None, min_length=1, max_length=200)
    verdict: Literal["useful", "not_useful"]
    reason: str = Field(pattern=r"^[a-z_]{1,40}$")
    note: str | None = Field(default=None, max_length=2000)


@router.post("/feedback", status_code=201)
async def give(
    body: FeedbackBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Record one verdict."""
    if (body.row_id is None) != (body.table is None):
        raise HTTPException(422, "a row needs its table")
    async with tenant_txn(ident.tenant_id) as s:
        rule = await exclusion_rule(s)
        account, run = await resolve_run(s, body.domain, body.run_id, rule)
        try:
            fid = await feedback.record(
                s,
                account_id=account,
                run_id=run if body.row_id else body.run_id,
                table_kind=body.table,
                row_id=body.row_id,
                verdict=body.verdict,
                reason_code=body.reason,
                note=body.note,
                user_id=ident.user_id,
                now=plane_of(request).clock.now(),
            )
        except feedback.FeedbackRefused as e:
            raise APIProblem(
                422, e.reason, "Feedback refused", "The feedback names an unknown reason or row."
            ) from None
    return {"id": fid}


@router.get("/feedback")
async def review(
    request: Request,
    domain: str | None = Query(default=None, max_length=253),
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """Current verdicts, optionally for one account."""
    async with tenant_txn(ident.tenant_id) as s:
        account = None
        if domain is not None:
            rule = await exclusion_rule(s)
            account, _ = await resolve_run(s, domain, None, rule)
        return {"feedback": await feedback.listing(s, account)}


class UsefulBody(BaseModel):
    """A usefulness event (identifiers only). Exports are recorded on download, not here."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["first_call_question_used", "confirmed_fact_referenced"]
    subject_id: UUID
    via: Literal["marked", "copied", "opened", "noted"]


@router.post("/usefulness", status_code=202)
async def useful(
    body: UsefulBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Record one event (a repeat the same day is accepted and not counted again)."""
    try:
        async with tenant_txn(ident.tenant_id) as s:
            new = await usefulness.record(
                s,
                kind=body.kind,
                subject=body.subject_id,
                via=body.via,
                user_id=ident.user_id,
                now=plane_of(request).clock.now(),
            )
    except usefulness.UnknownSubject:
        raise HTTPException(404, "not found") from None
    except ValueError:
        raise HTTPException(422, "unknown kind or channel") from None
    return {"recorded": new}


@router.get("/usefulness/metric")
async def tenant_metric(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The tenant's usefulness metric with its base."""
    async with tenant_txn(ident.tenant_id) as s:
        return await metric.tenant_metric(s, plane_of(request).clock.now())


@operator_router.get("/aggregate")
async def aggregate(
    request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """Per-tenant counts (no content)."""
    async with untenanted_session() as plain:
        return {"tenants": await metric.operator_aggregate(plain, plane_of(request).clock.now())}
