"""Run endpoints (``/v1/runs``). The tenant comes only from the verified token (W3).

* ``POST /v1/runs``: creates the run through ``runs.lifecycle.create_run`` (both rows, credit
  reservation, seeded tracker, one transaction) and enqueues slice 0. Same key and body replays
  (200 with the first run); same key, other body: 409 (defect 5); no credits: 402.
* ``GET /v1/runs/{id}``: status and the client-safe tracker view (leak-scanned).
* ``POST /v1/runs/{id}/approve``: AWAITING_APPROVAL to RUNNING and the next slice is enqueued
  (a run that requires a credit cap needs ``/cap/approve`` first, ``api.routes.spend``).
* ``POST /v1/runs/{id}/cancel``: ends the run CANCELED and settles the spend so far.
No endpoint accepts a tenant id, and none sends outreach.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api.auth import Identity, require_role
from account_intelligence.db.session import tenant_txn
from account_intelligence.runs import lifecycle
from account_intelligence.runs.lifecycle import (
    IdempotencyConflict,
    InsufficientCredits,
    RunNotFound,
    RunRequest,
    RunStateConflict,
)
from account_intelligence.workflow.transitions import RunStatus

router = APIRouter(prefix="/runs", tags=["runs"])
DOMAIN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$"


class RunBody(BaseModel):
    """What a client may send. No tenant field exists (and extra fields are refused)."""

    model_config = ConfigDict(extra="forbid")
    domain: str = Field(pattern=DOMAIN, max_length=253)
    depth: Literal["standard", "deep"] = "standard"
    lanes_off: list[str] = Field(default_factory=list, max_length=10)
    pursuing_client: bool = False


def _container(request: Request) -> Any:
    return request.app.state.container


@router.post("", status_code=202)
async def create_run(
    body: RunBody,
    request: Request,
    idem: str = Header(alias="Idempotency-Key", min_length=8, max_length=64),
    ident: Identity = Depends(require_role("admin", "seller")),
) -> dict[str, Any]:
    """Create a run (both rows, reservation, tracker in one transaction) and enqueue slice 0."""
    container = _container(request)
    run_input = {"lanes_off": sorted(set(body.lanes_off)), "pursuing_client": body.pursuing_client}
    req = RunRequest(body.domain, body.depth, idem, run_input)
    try:
        async with tenant_txn(ident.tenant_id) as s:
            deps_tracker = await _tracker_for(container, s)
            created = await lifecycle.create_run(
                s, ident.tenant_id, req, container.limits, deps_tracker
            )
    except IdempotencyConflict:
        raise HTTPException(409, "idempotency key reused with a different body") from None
    except InsufficientCredits:
        raise HTTPException(402, "insufficient credits") from None
    except RunStateConflict:
        raise HTTPException(409, "a concurrent request created this run; retry") from None
    if not created.replayed:
        await container.scheduler.enqueue_slice(ident.tenant_id, created.run_id, 0)
    return {"run_id": str(created.run_id), "status": created.status, "replayed": created.replayed}


async def _tracker_for(container: Any, session: Any) -> Any:
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.tracker.adapters import LedgerCallAdapter, RunInputAdapter
    from account_intelligence.tracker.repo import CoverageRepo
    from account_intelligence.tracker.service import TrackerService

    return TrackerService(
        CoverageRepo(session),
        LedgerCallAdapter(session, load_grading_config().category_by_operation),
        RunInputAdapter(session),
        owners=container.spec.coverage_owners,
    )


@router.get("/{run_id}")
async def get_run(
    run_id: UUID,
    request: Request,
    ident: Identity = Depends(require_role("admin", "seller", "viewer")),
) -> dict[str, Any]:
    """The run's status, end reason and client-safe coverage view (leak-scanned)."""
    container = _container(request)
    async with tenant_txn(ident.tenant_id) as s:
        row = (
            await s.execute(
                text("SELECT status, finished_reason FROM account_runs WHERE id = :r"),
                {"r": run_id},
            )
        ).first()
        if row is None:
            raise HTTPException(404, "run not found")
        view = await (await _tracker_for(container, s)).view(run_id, "client")
    return {
        "run_id": str(run_id),
        "status": row.status,
        "reason": row.finished_reason,
        "coverage": view,
    }


@router.post("/{run_id}/approve")
async def approve(
    run_id: UUID, request: Request, ident: Identity = Depends(require_role("admin", "seller"))
) -> dict[str, str]:
    """Approve a run waiting at the approval gate and enqueue its next slice. A run that requires
    a credit cap needs its cap approved first (``/cap/approve``, K2): 409 otherwise."""
    container = _container(request)
    try:
        async with tenant_txn(ident.tenant_id) as s:
            cap = (
                await s.execute(
                    text("SELECT cap_required, cap_version FROM account_runs WHERE id = :r"),
                    {"r": run_id},
                )
            ).first()
            if cap is not None and cap.cap_required and cap.cap_version is None:
                raise HTTPException(409, "approve the credit cap first (POST .../cap/approve)")
            await lifecycle.move(s, run_id, RunStatus.AWAITING_APPROVAL, RunStatus.RUNNING)
            used: int = (
                await s.execute(
                    text("SELECT slices_used FROM account_runs WHERE id = :r"), {"r": run_id}
                )
            ).scalar_one()
    except (RunNotFound, RunStateConflict):
        raise HTTPException(409, "run is not awaiting approval") from None
    await container.scheduler.enqueue_slice(ident.tenant_id, run_id, int(used))
    return {"run_id": str(run_id), "status": RunStatus.RUNNING.value}


@router.post("/{run_id}/cancel")
async def cancel(
    run_id: UUID, request: Request, ident: Identity = Depends(require_role("admin", "seller"))
) -> dict[str, str]:
    """Cancel a run: it ends CANCELED, unset rows are swept and the spend so far is settled."""
    container = _container(request)
    try:
        async with tenant_txn(ident.tenant_id) as s:
            status, _ = await lifecycle.finish(
                s,
                await _tracker_for(container, s),
                run_id,
                RunStatus.CANCELED,
                reason="canceled_by_user",
            )
    except RunNotFound:
        raise HTTPException(404, "run not found") from None
    except ValueError:
        raise HTTPException(409, "run cannot be canceled from its state") from None
    return {"run_id": str(run_id), "status": status}
