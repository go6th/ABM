"""Worker task endpoints, called by Cloud Tasks with a Google-signed OIDC token (D-18).

* ``POST /tasks/run-slice``: runs one slice of one run (``workflow.runner.execute_slice``).
* ``POST /tasks/approval-timeout``: expires an approval past its deadline (cancel and refund).

The body carries ids only. The token must be for the worker audience and from the scheduler's
service account (W3: no caller identity is trusted from the body). The tenant in the body is then
checked against the run row under RLS: a run that is not the tenant's is not found. In ``dev`` and
``test`` (no verifier configured) the endpoints answer only when ``ALLOW_UNAUTHENTICATED_TASKS=1``.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from account_intelligence.runs import lifecycle
from account_intelligence.workflow.runner import execute_slice

router = APIRouter(prefix="/tasks", tags=["tasks"])


class SliceTask(BaseModel):
    """Ids only (events carry no content)."""

    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    run_id: UUID
    slice: int = 0


class TimeoutTask(BaseModel):
    """The approval-timeout task body: ids only."""

    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    run_id: UUID


def _authorise(request: Request, authorization: str | None) -> Any:
    container = request.app.state.container
    if container.verify_task_token is None:
        if os.environ.get("ALLOW_UNAUTHENTICATED_TASKS") == "1" and container.env in (
            "dev",
            "test",
        ):
            return container
        raise HTTPException(403, "task authentication is not configured")
    token = (authorization or "").removeprefix("Bearer ").strip()
    try:
        container.verify_task_token(token, container.task_audience, container.task_service_account)
    except PermissionError:
        raise HTTPException(403, "invalid task token") from None
    return container


@router.post("/run-slice")
async def run_slice(
    task: SliceTask, request: Request, authorization: str | None = Header(default=None)
) -> dict[str, str]:
    """Run one slice of one run for an OIDC-verified Cloud Tasks call."""
    container = _authorise(request, authorization)
    from account_intelligence.platform.checkpoint import checkpointer_factory

    try:
        outcome = await execute_slice(
            container.deps_for,
            checkpointer_factory(container.checkpoint_dsn),
            container.scheduler,
            task.tenant_id,
            task.run_id,
            task.slice,
            settings=container.slices,
            retry_policy=container.extras.get("retry_policy"),
        )
    except LookupError:
        raise HTTPException(404, "run not found") from None
    if outcome.outcome == "busy":  # another worker holds the run: Cloud Tasks redelivers later
        raise HTTPException(503, "run is being processed by another worker")
    return {"outcome": outcome.outcome, "status": outcome.status}


@router.post("/approval-timeout")
async def approval_timeout(
    task: TimeoutTask, request: Request, authorization: str | None = Header(default=None)
) -> dict[str, bool]:
    """Expire a run's wait past its deadline: a first-cap approval cancels and refunds (known
    defect 8); a cap pause (D-45) records the timeout and resumes the run, which then ends
    BUDGET_EXCEEDED with partial results."""
    container = _authorise(request, authorization)
    deps = await container.deps_for(task.tenant_id, task.run_id)
    now = container.clock.now()
    async with tenant_txn(task.tenant_id) as s:
        expired = await lifecycle.expire_approval(s, deps.tracker(s), task.run_id, now)
        resumed = False if expired else await lifecycle.expire_cap_pause(s, task.run_id, now)
        used: int = (
            await s.execute(
                text("SELECT slices_used FROM account_runs WHERE id = :r"), {"r": task.run_id}
            )
        ).scalar_one()
    if resumed:
        await container.scheduler.enqueue_slice(task.tenant_id, task.run_id, int(used))
    return {"expired": expired or resumed}
