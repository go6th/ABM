"""Download everything (owner decision 2026-10-14, Phase 4B section 6; ``replica.downloads``).

* ``POST /v1/accounts/{domain}/download-everything``: start building the account's archive in the
  background (a repeated ``request_id`` returns the first job).
* ``GET /v1/downloads/{job_id}``: its status; when ready, its zip parts, each with a short-lived
  link (renewed on every read).
* ``GET /v1/downloads/{job_id}/parts/{n}?token=``: one part, with range requests (a download
  that broke off continues where it stopped). The link's token must match and be unexpired, and
  the job must be the caller's own (private workspaces).

Every start and every part fetched is an audited export.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.ratelimit import limit_user
from account_intelligence.api.routes.account_workspace import account_id_of
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import downloads as D

router = APIRouter(tags=["downloads"])
register(
    "POST",
    "/v1/accounts/{domain}/download-everything",
    tenant_route("export_files", "export", step_up=False),
)
register("GET", "/v1/downloads/{job_id}", tenant_route("read_public", "read", step_up=False))
register(
    "GET",
    "/v1/downloads/{job_id}/parts/{n}",
    tenant_route("export_files", "export", step_up=False),
)
RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
BLOCK = 1 << 20


class EverythingBody(BaseModel):
    """Start Download everything. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    request_id: UUID


@router.post(
    "/accounts/{domain}/download-everything", response_model=C.DownloadJob, status_code=202
)
async def start_everything(
    domain: str, body: EverythingBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Queue the archive build (see the module docstring)."""
    await limit_user(ident, "download_everything")
    container = request.app.state.container
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        job_id = await D.start(s, account_id, body.request_id)
        await audit_action(
            s,
            request,
            audit_class="export",
            action="download_everything",
            resource_type="account",
            resource_id=account_id,
        )
        out = await D.view(s, job_id, datetime.now(UTC))
    assert out is not None
    if out["status"] == "queued":
        scheduler = container.scheduler
        if hasattr(scheduler, "enqueue_download"):
            await scheduler.enqueue_download(ident.tenant_id, job_id)
    return out


@router.get("/downloads/{job_id}", response_model=C.DownloadJob)
async def download_status(job_id: UUID, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The archive's status and, when ready, its parts with fresh short-lived links."""
    async with tenant_txn(ident.tenant_id) as s:
        out = await D.view(s, job_id, datetime.now(UTC))
    if out is None:
        raise HTTPException(404, "not found")
    return out


def _range(header: str | None, size: int) -> tuple[int, int] | None:
    """The one byte range asked for (None: the whole part); 416 when it cannot be served."""
    if not header:
        return None
    match = RANGE.match(header.strip())
    if not match or (not match.group(1) and not match.group(2)):
        raise HTTPException(416, "range not satisfiable")
    if match.group(1):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else size - 1
    else:  # the last N bytes
        start, end = max(0, size - int(match.group(2))), size - 1
    if start >= size or end < start:
        raise HTTPException(416, "range not satisfiable")
    return start, min(end, size - 1)


@router.get("/downloads/{job_id}/parts/{n}")
async def download_part(
    job_id: UUID,
    n: int,
    request: Request,
    token: str = Query(min_length=20, max_length=200),
    range_header: str | None = Header(default=None, alias="range"),
    ident: Identity = Depends(authorize),
) -> Response:
    """One zip part, whole or a byte range (see the module docstring)."""
    container = request.app.state.container
    async with tenant_txn(ident.tenant_id) as s:
        try:
            part = await D.open_part(s, job_id, n, token, datetime.now(UTC))
        except D.LinkRefused:
            raise HTTPException(404, "this link has expired; open the download again") from None
        await audit_action(
            s,
            request,
            audit_class="export",
            action="download_everything_part",
            resource_type="download_job",
            resource_id=job_id,
        )
    from account_intelligence.api.routes.tasks import sources

    blobs = sources(container).blobs
    size = int(part["bytes"])
    wanted = _range(range_header, size)
    start, end = wanted if wanted is not None else (0, size - 1)

    def body() -> Any:
        """The bytes asked for, read in blocks."""
        with blobs.open(part["key"]) as reader:
            reader.seek(start)
            left = end - start + 1
            # LOOP(part download): cap=the range's bytes / 1 MiB; exit=range sent
            while left > 0:
                block = reader.read(min(BLOCK, left))
                if not block:
                    break
                left -= len(block)
                yield block

    headers = {
        "Content-Disposition": f'attachment; filename="{part["name"]}"',
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "no-store",
    }
    if wanted is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        body(), status_code=206 if wanted else 200, media_type="application/zip", headers=headers
    )
