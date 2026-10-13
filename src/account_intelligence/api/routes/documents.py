"""Uploaded documents for input mode "file" (``documents.service``).

* ``POST /v1/documents/uploads``: declare a document (name, size, type checked first) and get
  where to PUT it: a signed storage URL in the cloud, or ``PUT /v1/documents/{id}/content``
  with a one-time token locally. Bytes land in quarantine.
* ``POST /v1/documents/{id}/complete``: size and hash checked; the scan is enqueued. Nothing is
  parsed before the scan passed.
* ``GET /v1/documents/{id}/status``, ``GET /v1/documents``: scanning, parsing, indexed or failed,
  with the detected company.
* ``POST /v1/documents/confirm-company``: the user confirms the company; the documents are bound
  to its account and indexed.
* ``POST /v1/documents/estimate``: what a file-mode run would cost, before it starts.
* ``DELETE /v1/documents/{id}``: removes chunks, embeddings, passages, the redaction map and the
  stored object; facts citing it are marked for re-evaluation.
* ``GET``/``PUT /v1/documents/policy`` (PUT admin only): the redaction tier;
  ``POST /v1/documents/{id}/authoritative`` (admin only) marks a source authoritative.

Admins and sellers may upload; viewers may not. Every mutating handler records its audit event
in its own transaction. The tenant comes only from the verified identity.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity, require_role
from account_intelligence.api.errors import APIProblem
from account_intelligence.db.session import tenant_txn

router = APIRouter(prefix="/documents", tags=["documents"])
UPLOADERS = ("admin", "seller")
DOMAIN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$"
_STATUS = {
    "not_found": 404, "account_not_found": 404, "upload_closed": 409, "upload_expired": 410,
    "already_bound": 409, "not_uploaded": 409,
}  # fmt: skip


class UploadStart(BaseModel):
    """What a client declares before sending bytes. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0)
    mime: str | None = Field(default=None, max_length=200)
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


@router.post("/uploads", response_model=C.DocumentUploadStarted, status_code=201)
async def start_upload(
    body: UploadStart, request: Request, ident: Identity = Depends(require_role(*UPLOADERS))
) -> dict[str, Any]:
    """Declare a document and get its upload target (checked before any byte arrives)."""
    from documents.service import DocumentError

    try:
        out = await _docs(request).start_upload(
            ident.tenant_id, ident.user_id, body.name, body.size_bytes, body.mime, body.account_id
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
        account_id, _ = await lifecycle.add_account(s, ident.tenant_id, body.domain)
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
    async with tenant_txn(ident.tenant_id) as s:
        return await run_estimate(s, body.document_ids, body.enrich_public, cap)


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
