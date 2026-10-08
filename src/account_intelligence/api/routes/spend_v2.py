"""Credit manager v2 endpoints (thin; ``spend`` owns every rule). The tenant comes only from the
verified token or the operator's support session; no body carries a tenant, a principal or a
price.

Tenant plane (``/v1``):
* ``GET /v1/runs/{id}/spend/live``: the allow-listed tenant view (status, calls by category,
  ``as_of``); no amount, no provider (D-30).
* ``POST /v1/runs/{id}/cap/pauses/{request_id}/approve`` {amount_usd?}: an approver with a fresh
  step-up approves the suggested (or a custom) amount; a new cap version is appended and the
  paused run resumes.
* ``POST /v1/runs/{id}/cap/pauses/{request_id}/decline``: the run resumes and ends
  BUDGET_EXCEEDED with partial results.

Operator plane (``/operator/v1``, fail closed until the operator plane exists, D-33):
* ``GET .../runs/{id}/spend/live``: the operator view (amounts by provider code and category, the
  projection labelled as an estimate).
* ``POST .../runs/{id}/prepulled/intents`` {intent_id}: the exact file spec and a signed upload
  target in quarantine.
* ``POST .../runs/{id}/prepulled/{intent_id}/complete`` {account_confirmed, pull_date}: the one
  validation pass and its typed verdict.
* ``GET .../runs/{id}/prepulled/{intent_id}``: the intent's events (never file content).

GUARANTEE(test_pause_approval_route_needs_step_up_and_resumes_the_run): API contract.
GUARANTEE(test_tenant_live_route_never_shows_amounts_or_providers): D-30 at the API.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action, audit_refusal
from account_intelligence.api.auth import Identity, require_role
from account_intelligence.api.operator import OperatorIdentity, require_operator
from account_intelligence.db.session import tenant_txn
from account_intelligence.runs import lifecycle
from account_intelligence.runs.lifecycle import RunNotFound, RunStateConflict
from account_intelligence.workflow.transitions import RunStatus
from spend import live, pause, upload
from spend.config import load_spend_config, load_v2_config
from spend.errors import ApprovalInvalid, NotConfigured
from spend.money import MoneyError, usd_to_micros

router = APIRouter(prefix="/runs", tags=["spend-v2"])
operator_router = APIRouter(prefix="/operator/v1/runs", tags=["spend-v2-operator"])


class ApprovePauseBody(BaseModel):
    """An approval: optionally a custom amount (a decimal USD string, never a float)."""

    model_config = ConfigDict(extra="forbid")
    amount_usd: str | None = Field(default=None, pattern=r"^\d{1,6}(\.\d{1,6})?$")


class IntentBody(BaseModel):
    """Which upload request the operator is answering."""

    model_config = ConfigDict(extra="forbid")
    intent_id: UUID


class CompleteBody(BaseModel):
    """The operator's attestation (recorded with the verdict)."""

    model_config = ConfigDict(extra="forbid")
    account_confirmed: bool
    pull_date: date | None = None


def _approver(ident: Identity) -> pause.Approver:
    return pause.Approver(
        subject=ident.user_id,
        tenant_id=str(ident.tenant_id),
        role=ident.role,
        step_up_at=ident.step_up_at,
    )


async def _resume(request: Request, tenant_id: UUID, run_id: UUID) -> bool:
    """AWAITING_APPROVAL (cap) to RUNNING, then enqueue the next slice."""
    try:
        async with tenant_txn(tenant_id) as s:
            kind = (
                await s.execute(
                    text("SELECT awaiting_kind, slices_used FROM account_runs WHERE id = :r"),
                    {"r": run_id},
                )
            ).first()
            if kind is None or kind.awaiting_kind != "cap":
                return False
            await lifecycle.move(s, run_id, RunStatus.AWAITING_APPROVAL, RunStatus.RUNNING)
        await request.app.state.container.scheduler.enqueue_slice(
            tenant_id, run_id, int(kind.slices_used)
        )
        return True
    except (RunNotFound, RunStateConflict):
        return False


@router.get("/{run_id}/spend/live", response_model=C.TenantLiveSpend)
async def tenant_live(
    run_id: UUID,
    request: Request,
    ident: Identity = Depends(require_role("admin", "seller", "viewer")),
) -> dict[str, Any]:
    """The tenant's live spend status (allow-listed)."""
    now = request.app.state.container.clock.now()
    got = await live.snapshot(tenant_txn, ident.tenant_id, run_id, now)
    if got is None:
        raise HTTPException(404, "run not found")
    snap, by_category = got
    return snap.tenant_view(by_category)


@router.post("/{run_id}/cap/pauses/{request_id}/approve", response_model=C.PauseApproved)
async def approve_pause(
    run_id: UUID,
    request_id: UUID,
    body: ApprovePauseBody,
    request: Request,
    ident: Identity = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Approve a pause request (fresh step-up required); the run resumes."""
    now = request.app.state.container.clock.now()
    try:
        amount = usd_to_micros(body.amount_usd) if body.amount_usd is not None else None
        async with tenant_txn(ident.tenant_id) as s:
            cap = await pause.approve(
                s,
                run_id,
                request_id,
                _approver(ident),
                amount,
                now,
                load_v2_config(),
                load_spend_config(),
                request.app.state.container.wallet,
            )
            await audit_action(
                s,
                request,
                audit_class="run",
                action="pause_approve",
                resource_type="run",
                resource_id=run_id,
            )
    except ApprovalInvalid as exc:
        await audit_refusal(
            request,
            ident.tenant_id,
            audit_class="run",
            action="pause_approve",
            resource_type="run",
            resource_id=run_id,
        )
        raise HTTPException(403, f"approval refused: {exc}") from None
    except MoneyError as exc:
        raise HTTPException(422, f"invalid amount: {exc}") from None
    except NotConfigured:
        raise HTTPException(503, "wallet extension is not configured") from None
    resumed = await _resume(request, ident.tenant_id, run_id)
    return {
        "run_id": str(run_id),
        "request_id": str(request_id),
        "cap_micros": cap,
        "resumed": resumed,
    }


@router.post("/{run_id}/cap/pauses/{request_id}/decline", response_model=C.PauseDeclined)
async def decline_pause(
    run_id: UUID,
    request_id: UUID,
    request: Request,
    ident: Identity = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Decline a pause request; the run resumes to end BUDGET_EXCEEDED with partial results."""
    now = request.app.state.container.clock.now()
    try:
        async with tenant_txn(ident.tenant_id) as s:
            await pause.decline(s, run_id, request_id, _approver(ident), now, load_v2_config())
            await audit_action(
                s,
                request,
                audit_class="run",
                action="pause_decline",
                resource_type="run",
                resource_id=run_id,
            )
    except ApprovalInvalid as exc:
        await audit_refusal(
            request,
            ident.tenant_id,
            audit_class="run",
            action="pause_decline",
            resource_type="run",
            resource_id=run_id,
        )
        raise HTTPException(403, f"decline refused: {exc}") from None
    resumed = await _resume(request, ident.tenant_id, run_id)
    return {
        "run_id": str(run_id),
        "request_id": str(request_id),
        "decision": "declined",
        "resumed": resumed,
    }


@operator_router.get("/{run_id}/spend/live", response_model=C.OperatorLiveSpend)
async def operator_live(
    run_id: UUID, request: Request, op: OperatorIdentity = Depends(require_operator)
) -> dict[str, Any]:
    """The operator view of live spend (amounts by provider code, the labelled projection)."""
    now = request.app.state.container.clock.now()
    got = await live.snapshot(tenant_txn, op.tenant_id, run_id, now)
    if got is None:
        raise HTTPException(404, "run not found")
    snap = got[0]
    return {
        "run_id": snap.run_id,
        "as_of": snap.as_of,
        "cap_version": snap.cap_version,
        "cap_micros": snap.cap_micros,
        "soft_micros": snap.soft_micros,
        "committed_micros": snap.committed_micros,
        "held_micros": snap.held_micros,
        "remaining_micros": snap.remaining_micros,
        "mode": snap.mode.value,
        "by_provider": list(snap.by_provider),
        "projection": snap.projection,
    }


@operator_router.post("/{run_id}/prepulled/intents", response_model=C.UploadIntent)
async def intent_target(
    run_id: UUID,
    body: IntentBody,
    request: Request,
    op: OperatorIdentity = Depends(require_operator),
) -> dict[str, Any]:
    """The exact file spec and a signed quarantine upload target."""
    container = request.app.state.container
    try:
        spec, target = await upload.create_intent_target(
            tenant_txn,
            op.tenant_id,
            body.intent_id,
            container.upload_store,
            container.clock.now(),
            load_v2_config(),
        )
    except upload.UploadError as exc:
        raise HTTPException(404 if str(exc) == "intent_not_found" else 409, str(exc)) from None
    except NotConfigured:
        raise HTTPException(503, "upload store is not configured") from None
    return {
        "intent_id": str(body.intent_id),
        "spec": spec,
        "upload": {
            "url": target.url,
            "expires_at": target.expires_at.isoformat(),
            "max_bytes": target.max_bytes,
            "content_type": target.content_type,
        },
    }


@operator_router.post("/{run_id}/prepulled/{intent_id}/complete", response_model=C.UploadVerdict)
async def complete_upload(
    run_id: UUID,
    intent_id: UUID,
    body: CompleteBody,
    request: Request,
    op: OperatorIdentity = Depends(require_operator),
) -> dict[str, Any]:
    """Scan, validate and register the uploaded file; a typed verdict (never file content)."""
    container = request.app.state.container
    try:
        verdict = await upload.complete(
            tenant_txn,
            op.tenant_id,
            intent_id,
            f"operator:{op.operator_id}",
            upload.Attestation(body.account_confirmed, body.pull_date),
            container.upload_store,
            container.scanner,
            container.upload_parser,
            container.clock.now(),
            load_v2_config(),
        )
    except NotConfigured:
        raise HTTPException(503, "upload store is not configured") from None
    return {
        "intent_id": verdict.intent_id,
        "accepted": verdict.accepted,
        "reason_code": verdict.reason,
        "operations": list(verdict.operations),
        "record_count": verdict.record_count,
    }


@operator_router.get("/{run_id}/prepulled/{intent_id}", response_model=C.IntentEvents)
async def intent_status(
    run_id: UUID, intent_id: UUID, op: OperatorIdentity = Depends(require_operator)
) -> dict[str, Any]:
    """The intent's recorded events (codes and hashes only)."""
    async with tenant_txn(op.tenant_id) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT e.event, e.reason_code, e.at FROM prepulled_intent_events e"
                    " JOIN prepulled_intents i ON i.id = e.intent_id"
                    " WHERE e.intent_id = :i AND i.run_id = :r ORDER BY e.id"
                ),
                {"i": intent_id, "r": run_id},
            )
        ).all()
    if not rows:
        raise HTTPException(404, "intent not found")
    return {
        "intent_id": str(intent_id),
        "events": [
            {"event": r.event, "reason_code": r.reason_code, "at": r.at.isoformat()} for r in rows
        ],
    }
