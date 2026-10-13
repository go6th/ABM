"""Research files: chunked upload, status, audit report, analytics, questions, approval of the
model step, resume and delete (``research_files``, PLAN.md).

* Upload: ``POST /v1/files/uploads`` opens an upload; ``PUT .../parts/{n}`` streams one part's
  body to object storage (never held in memory); ``GET`` the upload lists the parts received;
  ``POST .../complete`` composes and hashes the file and links an existing file with the same
  SHA-256 (nothing is stored or processed twice) or creates it and enqueues processing.
* Reads: status (progress, findings by severity, model spend), the audit report (errors first,
  filter by severity), and the analytics document (profiles, sections, charts with sources).
* ``ask`` answers from the file's stored units with citations (gated like every model step).
* ``approve`` lets a model step above the approval limit run; ``resume`` continues a PARTIAL file
  from its last stored unit; ``DELETE`` removes the raw object and every derived row.

The tenant comes only from the verified identity; another tenant's file is "not found".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity, require_role
from account_intelligence.api.errors import APIProblem
from account_intelligence.db.session import tenant_txn

router = APIRouter(prefix="/files", tags=["files"])
SELLERS = ("admin", "seller")
EVERYONE = ("admin", "seller", "viewer")


class UploadInit(BaseModel):
    """What a client declares before sending parts. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=1024)
    size: int = Field(ge=0)
    mime: str | None = Field(default=None, max_length=200)


class Question(BaseModel):
    """A question about one file; ``approve`` accepts a cost above the approval limit."""

    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=2000)
    approve: bool = False


def _files(request: Request) -> Any:
    files = getattr(request.app.state.container, "files", None)
    if files is None:
        raise APIProblem(
            503, "files_unavailable", "Files unavailable", "File storage is not configured."
        )
    return files


def _problem(error: Any) -> APIProblem:
    return APIProblem(error.status, error.code, "Upload refused", str(error))


@router.post("/uploads", response_model=C.FileUploadStarted, status_code=201)
async def start_upload(
    body: UploadInit, request: Request, ident: Identity = Depends(require_role(*SELLERS))
) -> dict[str, Any]:
    """Open a resumable upload."""
    from research_files.upload import UploadError

    try:
        opened = dict(
            await _files(request).uploads.init(
                ident.tenant_id, ident.user_id, body.name, body.size, body.mime
            )
        )
    except UploadError as error:
        raise _problem(error) from None
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="run",
            action="file_upload_start",
            resource_type="research_file_upload",
            resource_id=opened["upload_id"],
        )
    return opened


@router.get("/uploads/{upload_id}", response_model=C.FileUploadState)
async def upload_state(
    upload_id: UUID, request: Request, ident: Identity = Depends(require_role(*SELLERS))
) -> dict[str, Any]:
    """The parts received so far (a client resumes by sending only the missing ones)."""
    from research_files.upload import UploadError

    try:
        return dict(await _files(request).uploads.state(ident.tenant_id, upload_id))
    except UploadError as error:
        raise _problem(error) from None


@router.put("/uploads/{upload_id}/parts/{part_no}", response_model=C.FileUploadPart)
async def upload_part(
    upload_id: UUID,
    part_no: int,
    request: Request,
    ident: Identity = Depends(require_role(*SELLERS)),
) -> dict[str, Any]:
    """Stream one part's request body to object storage."""
    from research_files.upload import UploadError

    async def body() -> AsyncIterator[bytes]:
        """The request body as it arrives (never buffered whole)."""
        async for chunk in request.stream():
            if chunk:
                yield chunk

    try:
        stored = dict(
            await _files(request).uploads.put_part(ident.tenant_id, upload_id, part_no, body())
        )
    except UploadError as error:
        raise _problem(error) from None
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="run",
            action="file_upload_part",
            resource_type="research_file_upload",
            resource_id=upload_id,
        )
    return stored


@router.post("/uploads/{upload_id}/complete", response_model=C.FileUploadCompleted)
async def complete_upload(
    upload_id: UUID, request: Request, ident: Identity = Depends(require_role(*SELLERS))
) -> dict[str, Any]:
    """Compose, hash and dedupe; a new file is queued for processing."""
    from research_files.upload import UploadError

    files = _files(request)
    try:
        done = await files.uploads.complete(ident.tenant_id, upload_id, ident.user_id)
    except UploadError as error:
        raise _problem(error) from None
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="run",
            action="file_dedupe" if done.deduplicated else "file_upload",
            resource_type="research_file",
            resource_id=done.file_id,
        )
    if not done.deduplicated:
        await files.enqueue(ident.tenant_id, done.file_id)
    return {"file_id": str(done.file_id), "status": done.status, "deduplicated": done.deduplicated}


@router.get("", response_model=C.FileList)
async def list_files(
    request: Request, ident: Identity = Depends(require_role(*EVERYONE))
) -> dict[str, Any]:
    """The workspace's research files, newest first."""
    from research_files import repo

    async with tenant_txn(ident.tenant_id) as s:
        rows = await repo.list_files(s)
    return {
        "files": [
            {
                "file_id": str(r.id),
                "name": r.name,
                "size_bytes": int(r.size_bytes),
                "detected_type": r.detected_type,
                "status": r.status,
                "stop_reason": r.stop_reason,
                "units_done": int(r.units_done),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


async def _row(tenant_id: UUID, file_id: UUID) -> Any:
    from research_files import repo

    async with tenant_txn(tenant_id) as s:
        row = await repo.get_file(s, file_id)
    if row is None:
        raise HTTPException(404, "file not found")
    return row


@router.get("/{file_id}", response_model=C.FileStatus)
async def file_status(
    file_id: UUID, request: Request, ident: Identity = Depends(require_role(*EVERYONE))
) -> dict[str, Any]:
    """Status, progress, findings by severity and model spend."""
    from research_files import repo

    row = await _row(ident.tenant_id, file_id)
    async with tenant_txn(ident.tenant_id) as s:
        _, counts = await repo.findings(s, file_id, None, 0, 0)
        spend = await repo.spend(s, file_id)
    return {
        "file_id": str(row.id),
        "name": row.name,
        "size_bytes": int(row.size_bytes),
        "sha256": row.sha256,
        "detected_type": row.detected_type,
        "status": row.status,
        "stop_reason": row.stop_reason,
        "stop_detail": row.stop_detail,
        "units_done": int(row.units_done),
        "findings": counts,
        "estimate": row.estimate,
        "spend": spend,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("/{file_id}/audit", response_model=C.FileAudit)
async def file_audit(
    file_id: UUID,
    request: Request,
    severity: Literal["info", "warn", "error"] | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    ident: Identity = Depends(require_role(*EVERYONE)),
) -> dict[str, Any]:
    """A page of the audit report (errors first) and the counts by severity."""
    from research_files import repo

    await _row(ident.tenant_id, file_id)
    async with tenant_txn(ident.tenant_id) as s:
        rows, counts = await repo.findings(s, file_id, severity, limit, offset)
    return {
        "file_id": str(file_id),
        "counts": counts,
        "findings": [
            {
                "severity": r.severity,
                "check": r.check_id,
                "location": r.location,
                "message": r.message,
                "fix": r.fix,
            }
            for r in rows
        ],
        "offset": offset,
        "limit": limit,
    }


@router.get("/{file_id}/analytics", response_model=C.FileAnalytics)
async def file_analytics(
    file_id: UUID, request: Request, ident: Identity = Depends(require_role(*EVERYONE))
) -> dict[str, Any]:
    """Profiles, document analytics and charts (every chart names its rows or pages)."""
    row = await _row(ident.tenant_id, file_id)
    return {"file_id": str(file_id), "status": row.status, "analytics": row.analytics}


@router.post("/{file_id}/ask", response_model=C.FileAnswer)
async def ask_file(
    file_id: UUID,
    body: Question,
    request: Request,
    ident: Identity = Depends(require_role(*SELLERS)),
) -> dict[str, Any]:
    """Answer from the file's stored units, citing them."""
    from research_files.qa import ask

    await _row(ident.tenant_id, file_id)
    try:
        answer = dict(
            await ask(_files(request).models, ident.tenant_id, file_id, body.question, body.approve)
        )
    except LookupError:
        raise HTTPException(404, "file not found") from None
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="run",
            action="file_ask",
            resource_type="research_file",
            resource_id=file_id,
        )
    return answer


async def _reprocess(
    request: Request, ident: Identity, file_id: UUID, action: str
) -> dict[str, Any]:
    from research_files import repo

    await _row(ident.tenant_id, file_id)
    async with tenant_txn(ident.tenant_id) as s:
        ok = await (repo.approve(s, file_id) if action == "approve" else repo.reopen(s, file_id))
    if not ok:
        raise HTTPException(409, f"the file cannot be {action}d in its current state")
    await _files(request).enqueue(ident.tenant_id, file_id)
    return {"file_id": str(file_id), "status": "PARSING"}


@router.post("/{file_id}/approve", response_model=C.FileState)
async def approve_file(
    file_id: UUID, request: Request, ident: Identity = Depends(require_role(*SELLERS))
) -> dict[str, Any]:
    """Approve the estimated model step and continue."""
    state = await _reprocess(request, ident, file_id, "approve")
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="run",
            action="file_approve",
            resource_type="research_file",
            resource_id=file_id,
        )
    return state


@router.post("/{file_id}/resume", response_model=C.FileState)
async def resume_file(
    file_id: UUID, request: Request, ident: Identity = Depends(require_role(*SELLERS))
) -> dict[str, Any]:
    """Continue a stopped file from its last stored unit (finished work costs nothing)."""
    state = await _reprocess(request, ident, file_id, "resume")
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="run",
            action="file_resume",
            resource_type="research_file",
            resource_id=file_id,
        )
    return state


@router.delete("/{file_id}", status_code=204)
async def delete_file(
    file_id: UUID, request: Request, ident: Identity = Depends(require_role(*SELLERS))
) -> Response:
    """Remove the raw object and every derived row (one deletion record is kept: hash, size)."""
    from research_files import repo
    from research_files.blobstore import file_key

    files = _files(request)
    async with tenant_txn(ident.tenant_id) as s:
        row = await repo.delete_file(s, file_id, ident.user_id)
        if row is None:
            raise HTTPException(404, "file not found")
        await audit_action(
            s,
            request,
            audit_class="run",
            action="file_delete",
            resource_type="research_file",
            resource_id=file_id,
        )
    await files.blobs.delete(file_key(ident.tenant_id, file_id))
    return Response(status_code=204)
