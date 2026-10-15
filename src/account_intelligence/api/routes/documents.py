"""Uploaded documents for input mode "file" (``documents.service``).

* ``POST /v1/documents/uploads``: declare a document (name, size, type checked first; the size
  against the admin's per-file limit) and get where to send it: a resumable session straight
  to object storage, sent in ``chunk_bytes`` chunks (``protocol: resumable``), or, only where the
  store has none, ``PUT /v1/documents/{id}/content`` with a one-time token. Bytes land in
  quarantine; a file never passes through the web app.
* ``POST /v1/documents/{id}/complete``: size checked; the scan is enqueued. Nothing is parsed
  before the scan passed.
* ``GET /v1/documents/drive-files?q=``: the caller's own Google Drive files a research can read
  (Mail and drive connection; listed on the server, the token never reaches the browser).
* ``POST /v1/documents/from-drive``: one of those files, downloaded (or exported, for Google
  Docs, Sheets and Slides) on the server into quarantine and completed: from there it is scanned
  and read exactly like an upload.
* ``GET /v1/documents/{id}/status``, ``GET /v1/documents``: scanning, reading (parts done of
  parts total), indexed or failed, with the detected company and each failed part's reason.
* ``POST /v1/documents/{id}/parts/{n}/retry``: "Try again" on a failed part (n from 1).
* ``POST /v1/documents/confirm-company``: the user confirms the company; the documents are bound
  to its account and indexed.
* ``POST /v1/documents/estimate``: what a file-mode run would cost, before it starts.
* ``DELETE /v1/documents/{id}``: removes chunks, embeddings, passages, the redaction map and the
  stored object; facts citing it are marked for re-evaluation.
* ``GET``/``PUT /v1/documents/policy`` (PUT admin only): the redaction tier;
  ``POST /v1/documents/{id}/authoritative`` (admin only) marks a source authoritative.

Admins, analysts and sellers may upload; viewers may not. Every mutating handler records its
audit event in its own transaction. The tenant comes only from the verified identity.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity, require_role
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.ratelimit import limit_user
from account_intelligence.db.session import tenant_txn

router = APIRouter(prefix="/documents", tags=["documents"])
UPLOADERS = ("admin", "seller")  # require_role admits analyst wherever seller is
DOMAIN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$"
_STATUS = {
    "not_found": 404, "account_not_found": 404, "upload_closed": 409, "upload_expired": 410,
    "already_bound": 409, "not_uploaded": 409, "part_not_failed": 409,
}  # fmt: skip


class UploadStart(BaseModel):
    """What a client declares before sending bytes. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0)
    mime: str | None = Field(default=None, max_length=200)
    account_id: UUID | None = None


class FromDrive(BaseModel):
    """A file from the caller's own Google Drive. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    file_id: str = Field(min_length=10, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    account_id: UUID | None = None


class ConfirmCompany(BaseModel):
    """The company the user confirmed for these documents."""

    model_config = ConfigDict(extra="forbid")
    document_ids: list[UUID] = Field(min_length=1, max_length=100)
    company_name: str = Field(min_length=1, max_length=200)
    domain: str = Field(pattern=DOMAIN, max_length=253)


class EstimateBody(BaseModel):
    """What a file-mode run would read and whether it enriches from public sources."""

    model_config = ConfigDict(extra="forbid")
    document_ids: list[UUID] = Field(min_length=1, max_length=100)
    enrich_public: bool = False
    credit_cap: float | None = Field(default=None, gt=0, le=10000)


class PolicyBody(BaseModel):
    """The tenant's redaction tier."""

    model_config = ConfigDict(extra="forbid")
    redaction_tier: Literal["strict", "standard", "minimal"]


class FlagBody(BaseModel):
    """Whether a document is an authoritative source."""

    model_config = ConfigDict(extra="forbid")
    authoritative: bool


def _docs(request: Request) -> Any:
    docs = getattr(request.app.state.container, "documents", None)
    if docs is None:
        raise APIProblem(
            503,
            "documents_unavailable",
            "Documents unavailable",
            "Document storage is not configured.",
        )
    return docs


def _problem(error: Any) -> APIProblem:
    status = _STATUS.get(error.code, 422)
    return APIProblem(status, error.code, "Document refused", str(error))


def _origin() -> str | None:
    """The web app's origin, for the storage session's CORS (the app's own public URL)."""
    from urllib.parse import urlsplit

    from account_intelligence.config import get_settings

    base = get_settings().public_base_url
    if not base:
        return None
    parts = urlsplit(base)
    return f"{parts.scheme}://{parts.netloc}"


@router.post("/uploads", response_model=C.DocumentUploadStarted, status_code=201)
async def start_upload(
    body: UploadStart, request: Request, ident: Identity = Depends(require_role(*UPLOADERS))
) -> dict[str, Any]:
    """Declare a document and get its upload target (checked before any byte arrives)."""
    await limit_user(ident, "document_upload")
    from documents.service import DocumentError

    try:
        out = await _docs(request).start_upload(
            ident.tenant_id,
            ident.user_id,
            body.name,
            body.size_bytes,
            body.mime,
            body.account_id,
            origin=_origin(),
        )
    except DocumentError as error:
        raise _problem(error) from None
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="run",
            action="document_upload_start",
            resource_type="document",
            resource_id=out["document_id"],
        )
    return dict(out)


_DRIVE_STATUS = {
    "not_available": 409, "not_allowed": 403, "drive_not_connected": 409, "drive_broken": 409,
    "drive_file_not_found": 404, "too_large": 422, "wrong_type": 422, "empty": 422,
}  # fmt: skip


async def _drive_token(request: Request, ident: Identity) -> str:
    """The caller's own fresh Drive access token (server side only)."""
    from account_intelligence.api.routes.maildrive import _apps, _oauth
    from account_intelligence.api.tenant_plane import plane_of
    from connectors_client.drive_files import DriveUnavailable, user_drive_token

    try:
        return await user_drive_token(
            tenant_txn,
            ident.tenant_id,
            ident.user_id,
            oauth=_oauth(request),
            store=plane_of(request).secrets,
            apps=_apps(request),
        )
    except DriveUnavailable as refused:
        raise _drive_problem(refused) from None


def _drive_problem(refused: Any) -> APIProblem:
    status = _DRIVE_STATUS.get(refused.code, 422)
    return APIProblem(status, refused.code, "Google Drive", str(refused))


def _drive_files(request: Request) -> Any:
    from connectors_client.drive_files import HttpDriveFiles

    found = request.app.state.container.extras.get("drive_files")
    return found if found is not None else HttpDriveFiles()


@router.get("/drive-files", response_model=C.DriveFileList)
async def drive_files(
    request: Request,
    q: str = Query(default="", max_length=100),
    ident: Identity = Depends(require_role(*UPLOADERS)),
) -> dict[str, Any]:
    """The caller's newest Drive files a research can read, optionally by name."""
    await limit_user(ident, "drive_list")
    token = await _drive_token(request, ident)
    try:
        files = await _drive_files(request).list(token, q)
    except Exception as error:  # noqa: BLE001 - Google's failure is shown, never its detail
        raise APIProblem(
            502, "drive_unreachable", "Google Drive", "Google Drive did not answer. Try again."
        ) from error
    return {
        "files": [
            {
                "id": f.id,
                "name": f.import_name,
                "type": f.extension,
                "size_bytes": f.size,
                "modified_at": f.modified,
                "google_native": f.export_mime is not None,
            }
            for f in files
        ]
    }


@router.post("/from-drive", response_model=C.DocumentStatus, status_code=201)
async def import_from_drive(
    body: FromDrive, request: Request, ident: Identity = Depends(require_role(*UPLOADERS))
) -> dict[str, Any]:
    """Fetch one of the caller's Drive files on the server into quarantine and enqueue its scan."""
    await limit_user(ident, "document_upload")
    from connectors_client.drive_files import DriveUnavailable
    from documents.service import DocumentError

    docs = _docs(request)
    token = await _drive_token(request, ident)
    reader = _drive_files(request)
    spool = None
    try:
        file = await reader.get(token, body.file_id)
        spool = await reader.download(token, file, await docs.file_limit(ident.tenant_id))
        out = await docs.import_file(
            ident.tenant_id,
            ident.user_id,
            file.import_name,
            spool,
            file.export_mime or file.mime,
            body.account_id,
        )
    except DriveUnavailable as refused:
        raise _drive_problem(refused) from None
    except DocumentError as error:
        raise _problem(error) from None
    except APIProblem:
        raise
    except Exception as error:  # noqa: BLE001 - Google's failure is shown, never its detail
        raise APIProblem(
            502, "drive_unreachable", "Google Drive", "Google Drive did not answer. Try again."
        ) from error
    finally:
        if spool is not None:
            spool.unlink(missing_ok=True)
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="run",
            action="document_drive_import",
            resource_type="document",
            resource_id=out["document_id"],
        )
    return dict(out)


@router.put("/{document_id}/content", response_model=C.DocumentReceived)
async def upload_content(
    document_id: UUID,
    request: Request,
    token: str = Query(min_length=20, max_length=200),
    ident: Identity = Depends(require_role(*UPLOADERS)),
) -> dict[str, Any]:
    """The local stand-in for a signed URL: the body streams into quarantine, never parsed."""
    from documents.service import DocumentError

    async def body() -> AsyncIterator[bytes]:
        """The request body as it arrives (never buffered whole)."""
        async for chunk in request.stream():
            if chunk:
                yield chunk

    try:
        size = await _docs(request).receive(ident.tenant_id, document_id, token, body())
    except DocumentError as error:
        raise _problem(error) from None
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="run",
            action="document_upload",
            resource_type="document",
            resource_id=document_id,
        )
    return {"document_id": str(document_id), "received_bytes": size}


@router.post("/{document_id}/complete", response_model=C.DocumentStatus)
async def complete(
    document_id: UUID, request: Request, ident: Identity = Depends(require_role(*UPLOADERS))
) -> dict[str, Any]:
    """Check what arrived and enqueue the scan."""
    from documents.service import DocumentError

    try:
        out = await _docs(request).complete(ident.tenant_id, document_id)
    except DocumentError as error:
        raise _problem(error) from None
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="run",
            action="document_upload_complete",
            resource_type="document",
            resource_id=document_id,
        )
    return dict(out)


@router.get("/{document_id}/status", response_model=C.DocumentStatus)
async def status(
    document_id: UUID, request: Request, ident: Identity = Depends(require_role(*UPLOADERS))
) -> dict[str, Any]:
    """The document's status and detected company."""
    from documents.service import DocumentError

    try:
        return dict(await _docs(request).status(ident.tenant_id, document_id))
    except DocumentError as error:
        raise _problem(error) from None


@router.post("/{document_id}/parts/{part}/retry", response_model=C.DocumentStatus)
async def retry_part(
    document_id: UUID,
    part: int,
    request: Request,
    ident: Identity = Depends(require_role(*UPLOADERS)),
) -> dict[str, Any]:
    """Try a failed part again (``part`` counts from 1, as the screen shows it)."""
    from documents.service import DocumentError

    await limit_user(ident, "part_retry")

    if part < 1:
        raise APIProblem(404, "not_found", "Not found", "No such part.")
    try:
        out = await _docs(request).retry_part(ident.tenant_id, document_id, part - 1)
    except DocumentError as error:
        raise _problem(error) from None
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="run",
            action="document_part_retry",
            resource_type="document",
            resource_id=document_id,
        )
    return dict(out)


@router.get("/{document_id}/passages", response_model=C.DocumentPassages)
async def passages(
    document_id: UUID,
    request: Request,
    after: int = Query(default=-1, ge=-1),
    limit: int = Query(default=50, ge=1, le=200),
    ident: Identity = Depends(require_role(*UPLOADERS)),
) -> dict[str, Any]:
    """What was read from one document: its passages in order, each with where it came from
    (page, slide, row), a page at a time (audit 2026-10-15: the user could not see what a file
    contributed). The caller's own documents only (row level security)."""
    from documents import repo
    from documents.chunking import cite

    async with tenant_txn(ident.tenant_id) as s:
        found = (
            await s.execute(text("SELECT 1 FROM documents WHERE id = :d"), {"d": document_id})
        ).first()
        if found is None:
            raise APIProblem(404, "not_found", "Not found", "The document was not found.")
        rows = await repo.document_passages_page(s, document_id, after, limit)
    out = [
        {
            "seq": int(r.seq),
            "kind": str(r.kind),
            "where": cite(r.location) if isinstance(r.location, dict) else None,
            "heading": r.heading_path,
            "content": str(r.content),
        }
        for r in rows
    ]
    return {
        "document_id": str(document_id),
        "passages": out,
        "next_after": out[-1]["seq"] if len(out) == limit else None,
    }


@router.get("", response_model=C.DocumentList)
async def list_documents(
    request: Request,
    account_id: UUID | None = Query(default=None),
    ident: Identity = Depends(require_role(*UPLOADERS)),
) -> dict[str, Any]:
    """The tenant's documents, newest first (one account's when given)."""
    return {"documents": await _docs(request).list(ident.tenant_id, account_id)}


@router.post("/confirm-company", response_model=C.CompanyConfirmed)
async def confirm_company(
    body: ConfirmCompany, request: Request, ident: Identity = Depends(require_role(*UPLOADERS))
) -> dict[str, Any]:
    """Bind the documents to the confirmed company's account and index them."""
    from account_intelligence.runs import lifecycle
    from documents.service import DocumentError

    async with tenant_txn(ident.tenant_id) as s:
        account_id, _ = await lifecycle.add_account(
            s, ident.tenant_id, body.domain, added_by=ident.user_id
        )
        await lifecycle.name_account(s, account_id, body.company_name)
    try:
        bound = await _docs(request).confirm_company(ident.tenant_id, body.document_ids, account_id)
    except DocumentError as error:
        raise _problem(error) from None
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="run",
            action="document_company_confirm",
            resource_type="document",
            resource_id=account_id,
        )
    return {
        "account_id": str(account_id),
        "domain": body.domain,
        "document_ids": [str(d) for d in bound],
    }


@router.post("/estimate", response_model=C.DocumentEstimate)
async def estimate(
    body: EstimateBody, request: Request, ident: Identity = Depends(require_role(*UPLOADERS))
) -> dict[str, Any]:
    """What a file-mode run over these documents would cost (shown before it starts)."""
    from documents.estimate import estimate as run_estimate

    container = request.app.state.container
    cap = body.credit_cap if body.credit_cap is not None else float(container.limits.budget_usd)
    from datetime import UTC, datetime

    from account_intelligence.replica import credits as CR

    async with tenant_txn(ident.tenant_id) as s:
        out = await run_estimate(s, body.document_ids, body.enrich_public, cap)
        # reading the client's own files costs no credits; public sources cost the rate table's
        # estimate, reserved only when the user confirms (FIX_LOG Phase 4.5)
        out["credits"] = (
            (await CR.estimate(s, None, "file_public")).credits if body.enrich_public else 0
        )
        out["credits_left"] = (await CR.pool(s, datetime.now(UTC)))["left"]
        return out


@router.delete("/{document_id}", response_model=C.DocumentDeleted)
async def delete(
    document_id: UUID, request: Request, ident: Identity = Depends(require_role(*UPLOADERS))
) -> dict[str, Any]:
    """Remove the document and everything derived from it; mark facts for re-evaluation."""
    from documents.service import DocumentError

    try:
        out = await _docs(request).delete(ident.tenant_id, document_id, ident.user_id)
    except DocumentError as error:
        raise _problem(error) from None
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="run",
            action="document_delete",
            resource_type="document",
            resource_id=document_id,
        )
    return dict(out)


@router.get("/policy", response_model=C.DocumentPolicy)
async def get_policy(
    request: Request, ident: Identity = Depends(require_role(*UPLOADERS))
) -> dict[str, Any]:
    """The tenant's redaction tier."""
    from documents import repo
    from documents.config import load_config

    async with tenant_txn(ident.tenant_id) as s:
        return {"redaction_tier": await repo.policy_tier(s, load_config().redaction.default_tier)}


@router.put("/policy", response_model=C.DocumentPolicy)
async def put_policy(
    body: PolicyBody, request: Request, ident: Identity = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Set the redaction tier for documents parsed from now on (admin)."""
    from documents import repo

    async with tenant_txn(ident.tenant_id) as s:
        await repo.set_policy(s, body.redaction_tier, ident.user_id)
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="document_policy",
            resource_type="tenant",
            resource_id=None,
        )
    return {"redaction_tier": body.redaction_tier}


@router.post("/{document_id}/authoritative", response_model=C.DocumentFlag)
async def set_authoritative(
    document_id: UUID,
    body: FlagBody,
    request: Request,
    ident: Identity = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Mark a document an authoritative source (admin): later runs may grade its facts
    Confirmed; they stay private (Internal Only) all the same."""
    from documents import repo

    async with tenant_txn(ident.tenant_id) as s:
        if not await repo.set_authoritative(s, document_id, body.authoritative):
            raise APIProblem(404, "not_found", "Document not found", "The document was not found.")
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="document_authoritative",
            resource_type="document",
            resource_id=document_id,
        )
    return {"document_id": str(document_id), "authoritative": body.authoritative}
