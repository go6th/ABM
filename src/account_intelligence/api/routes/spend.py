"""Credit cap endpoints (thin; ``spend`` owns the rules). The tenant and the approver come only
from the verified token; no endpoint accepts a tenant id or a principal in the body.

* ``GET /v1/runs/{id}/cap``: the latest proposal's version, hash, cap, state and calls by
  category, so an approver can bind an approval to what was shown (no provider, D-30).
* ``POST /v1/runs/{id}/cap/approve`` {version, proposal_hash}: binds the approval to the shown
  proposal (K3), writes the run's enforcement columns, then moves an AWAITING_APPROVAL run on.
* ``POST /v1/runs/{id}/cap/reject`` {version, reason}.
* ``POST /v1/runs/{id}/cap/extend`` {cap_usd, reason}: an extension request on an attended run.
* ``GET /v1/runs/{id}/spend``: the tenant spend view (categories and call counts only, D-30).
The internal operator report is not served here: it is never a client file (K13).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_refusal, audited_txn
from account_intelligence.api.auth import Identity, require_role
from account_intelligence.db.session import tenant_txn
from account_intelligence.runs import lifecycle
from account_intelligence.runs.lifecycle import RunNotFound, RunStateConflict
from account_intelligence.workflow.transitions import RunStatus
from spend import service
from spend.config import load_spend_config
from spend.errors import ApprovalInvalid, ExtensionDenied, ProposalStale
from spend.models import Principal
from spend.money import MoneyError, usd_to_micros

router = APIRouter(prefix="/runs", tags=["spend"])


class ApproveBody(BaseModel):
    """What a person approves: the version and the hash of the proposal they were shown."""

    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RejectBody(BaseModel):
    """A rejection with its reason."""

    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=300)


class ExtendBody(BaseModel):
    """An extension request: a decimal string in USD (never a float) and the reason."""

    model_config = ConfigDict(extra="forbid")
    cap_usd: str = Field(pattern=r"^\d{1,6}(\.\d{1,6})?$")
    reason: str = Field(min_length=1, max_length=300)


def _principal(ident: Identity) -> Principal:
    return Principal(
        kind="human", subject=ident.user_id, tenant_id=str(ident.tenant_id), role=ident.role
    )


@router.get("/{run_id}/cap", response_model=C.CapProposalView)
async def current_cap(
    run_id: UUID, ident: Identity = Depends(require_role("admin", "seller", "viewer"))
) -> dict[str, Any]:
    """The latest cap proposal of the run; 404 when none exists for this tenant."""
    view = await service.current_proposal(tenant_txn, ident.tenant_id, run_id)
    if view is None:
        raise HTTPException(404, "no cap proposal for this run")
    return view


@router.post("/{run_id}/cap/approve", response_model=C.CapApproved)
async def approve_cap(
    run_id: UUID,
    body: ApproveBody,
    request: Request,
    ident: Identity = Depends(require_role("admin", "seller")),
) -> dict[str, Any]:
    """Approve the shown cap proposal; an AWAITING_APPROVAL run then resumes."""
    cfg = load_spend_config()
    clock = request.app.state.container.clock
    try:
        cap = await service.approve(
            audited_txn(
                request,
                audit_class="run",
                action="cap_approve",
                resource_type="run",
                resource_id=run_id,
            ),
            ident.tenant_id,
            run_id,
            body.version,
            body.proposal_hash,
            _principal(ident),
            cfg,
            clock.now(),
        )
    except ProposalStale as exc:
        raise HTTPException(409, f"proposal stale: {exc}") from None
    except ApprovalInvalid as exc:
        await audit_refusal(
            request,
            ident.tenant_id,
            audit_class="run",
            action="cap_approve",
            resource_type="run",
            resource_id=run_id,
        )
        raise HTTPException(403, f"approval refused: {exc}") from None
    resumed = False
    try:
        async with tenant_txn(ident.tenant_id) as s:
            await lifecycle.move(s, run_id, RunStatus.AWAITING_APPROVAL, RunStatus.RUNNING)
            used: int = (
                await s.execute(
                    text("SELECT slices_used FROM account_runs WHERE id = :r"), {"r": run_id}
                )
            ).scalar_one()
        await request.app.state.container.scheduler.enqueue_slice(
            ident.tenant_id, run_id, int(used)
        )
        resumed = True
    except (RunNotFound, RunStateConflict):
        resumed = False  # an extension on a running run, or a run that is not waiting
    return {"run_id": str(run_id), "version": body.version, "cap_micros": cap, "resumed": resumed}


@router.post("/{run_id}/cap/reject", response_model=C.CapRejected)
async def reject_cap(
    run_id: UUID,
    body: RejectBody,
    request: Request,
    ident: Identity = Depends(require_role("admin", "seller")),
) -> dict[str, Any]:
    """Reject a cap version (the run then has no approved cap: no paid call)."""
    try:
        await service.reject(
            audited_txn(
                request,
                audit_class="run",
                action="cap_reject",
                resource_type="run",
                resource_id=run_id,
            ),
            ident.tenant_id,
            run_id,
            body.version,
            _principal(ident),
            load_spend_config(),
            body.reason,
        )
    except ApprovalInvalid as exc:
        await audit_refusal(
            request,
            ident.tenant_id,
            audit_class="run",
            action="cap_reject",
            resource_type="run",
            resource_id=run_id,
        )
        raise HTTPException(403, f"rejection refused: {exc}") from None
    return {"run_id": str(run_id), "version": body.version, "decision": "rejected"}


@router.post("/{run_id}/cap/extend", response_model=C.CapExtended)
async def extend_cap(
    run_id: UUID,
    body: ExtendBody,
    request: Request,
    ident: Identity = Depends(require_role("admin", "seller")),
) -> dict[str, Any]:
    """Request an extension (a new version that needs its own approval)."""
    try:
        version = await service.request_extension(
            audited_txn(
                request,
                audit_class="run",
                action="cap_extend",
                resource_type="run",
                resource_id=run_id,
            ),
            ident.tenant_id,
            run_id,
            usd_to_micros(body.cap_usd),
            body.reason,
            load_spend_config(),
        )
    except (ExtensionDenied, MoneyError) as exc:
        await audit_refusal(
            request,
            ident.tenant_id,
            audit_class="run",
            action="cap_extend",
            resource_type="run",
            resource_id=run_id,
        )
        raise HTTPException(409, f"extension refused: {exc}") from None
    return {"run_id": str(run_id), "version": version, "status": "extension_requested"}


@router.get("/{run_id}/spend", response_model=C.JsonObject)
async def tenant_spend(
    run_id: UUID, ident: Identity = Depends(require_role("admin", "seller", "viewer"))
) -> dict[str, Any]:
    """The tenant spend view of the latest report (categories and call counts only); the caller's
    own runs only (the run is checked under the owner policy before the reader role, whose
    policy on spend_reports is tenant-wide)."""
    async with tenant_txn(ident.tenant_id) as s:
        mine = (
            await s.execute(text("SELECT 1 FROM account_runs WHERE id = :r"), {"r": run_id})
        ).first()
        if mine is None:
            raise HTTPException(404, "no spend view for this run yet")
        await s.execute(text("SET LOCAL ROLE public_reader"))
        row = (
            await s.execute(
                text(
                    "SELECT tenant_json FROM spend_reports WHERE run_id = :r"
                    " ORDER BY version DESC LIMIT 1"
                ),
                {"r": run_id},
            )
        ).first()
    if row is None:
        raise HTTPException(404, "no spend view for this run yet")
    return dict(row.tenant_json)
