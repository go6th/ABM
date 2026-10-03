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
    return {"outcome": outcome.outcome, "status": outcome.status}


@router.post("/approval-timeout")
async def approval_timeout(
    task: TimeoutTask, request: Request, authorization: str | None = Header(default=None)
) -> dict[str, bool]:
    container = _authorise(request, authorization)
    deps = await container.deps_for(task.tenant_id, task.run_id)
    async with tenant_txn(task.tenant_id) as s:
        expired = await lifecycle.expire_approval(
            s, deps.tracker(s), task.run_id, container.clock.now()
        )
    return {"expired": expired}
