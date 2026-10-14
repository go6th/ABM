"""Worker task endpoints, called by Cloud Tasks with a Google-signed OIDC token (D-18).

* ``POST /tasks/run-slice``: runs one slice of one run (``workflow.runner.execute_slice``); an ABM
  run (Phase 1 only, Phase 2 only, or a tenant with the ``abm_agents`` flag) runs the agent
  layer's graph under the same slice lifecycle (``agents.production.wiring``).
* ``POST /tasks/approval-timeout``: expires an approval past its deadline (cancel and refund).
* ``POST /tasks/research-job``: runs one confirmed research job within its reserved credits.

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
    from agents.production.wiring import AbmGraphFactory, uses_abm
    from documents.graph import FileGraphFactory, is_file_run

    try:
        deps = await container.deps_for(task.tenant_id, task.run_id)
        factory: Any = None
        if await is_file_run(deps):
            # input mode "file": the uploaded documents only (documents.graph)
            if getattr(container, "documents", None) is None:
                raise HTTPException(503, "document processing is not configured")
            factory = FileGraphFactory(container.documents)
        elif await uses_abm(deps):
            factory = AbmGraphFactory()
        outcome = await execute_slice(
            container.deps_for,
            checkpointer_factory(container.checkpoint_dsn),
            container.scheduler,
            task.tenant_id,
            task.run_id,
            task.slice,
            settings=container.slices,
            retry_policy=container.extras.get("retry_policy"),
            graph_factory=factory,
        )
    except LookupError:
        raise HTTPException(404, "run not found") from None
    if outcome.outcome == "busy":  # another worker holds the run: Cloud Tasks redelivers later
        raise HTTPException(503, "run is being processed by another worker")
    return {"outcome": outcome.outcome, "status": outcome.status}


class FileTask(BaseModel):
    """The file-processing task body: ids only."""

    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    file_id: UUID


@router.post("/file-process")
async def file_process(
    task: FileTask, request: Request, authorization: str | None = Header(default=None)
) -> dict[str, str]:
    """Process or resume one research file (``research_files.pipeline``). The tenant in the body
    is checked against the file row under RLS: another tenant's file is not found."""
    container = _authorise(request, authorization)
    files = getattr(container, "files", None)
    if files is None:
        raise HTTPException(503, "file processing is not configured")
    outcome = await files.processor().process(task.tenant_id, task.file_id)
    if outcome == "busy":
        raise HTTPException(503, "the file is being processed by another worker")
    return {"outcome": outcome}


class DocumentTask(BaseModel):
    """The document-processing task body: ids only."""

    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    document_id: UUID


@router.post("/document-process")
async def document_process(
    task: DocumentTask, request: Request, authorization: str | None = Header(default=None)
) -> dict[str, str]:
    """Scan, parse and index one uploaded document (``documents.service``). The tenant in the
    body is checked against the document row under RLS: another tenant's document is not found."""
    container = _authorise(request, authorization)
    docs = getattr(container, "documents", None)
    if docs is None:
        raise HTTPException(503, "document processing is not configured")
    outcome = await docs.process(task.tenant_id, task.document_id)
    if outcome == "busy":
        raise HTTPException(503, "the document is being processed by another worker")
    return {"outcome": outcome}


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


class ResearchJobTask(BaseModel):
    """A confirmed research job to run: ids only."""

    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    job_id: UUID


@router.post("/research-job")
async def research_job(
    task: ResearchJobTask, request: Request, authorization: str | None = Header(default=None)
) -> dict[str, str]:
    """Run one confirmed research job (``replica.jobs.run_job``). The tenant in the body is
    checked against the job row under RLS: another tenant's job is simply not claimed."""
    _authorise(request, authorization)
    from account_intelligence.replica import jobs

    engine = jobs.engine_from_env(request.app.state.container.env)
    return {"outcome": await jobs.run_job(task.tenant_id, task.job_id, engine)}
