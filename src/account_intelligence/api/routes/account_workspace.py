"""The Account workspace screen and its deliverable files.

* ``GET /v1/accounts/{domain}/workspace``: narrative, key findings, what changed (after a refresh
  only), deep-dive counts and files.
* ``GET /v1/accounts/{domain}/deliverables``: the newest version of each deliverable.
* ``GET /v1/accounts/{domain}/deliverables/download?ids=a,b``: one id downloads that file as
  itself; several (or ``all``) download one zip. Every download is an audited export. A file of
  another account or tenant is simply not found.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import workspace as W
from tenancy.storage import PathRefused, require_owned

router = APIRouter(prefix="/accounts", tags=["accounts"])
register(
    "GET", "/v1/accounts/{domain}/workspace", tenant_route("read_public", "read", step_up=False)
)
register(
    "GET", "/v1/accounts/{domain}/deliverables", tenant_route("read_public", "read", step_up=False)
)
register(
    "GET",
    "/v1/accounts/{domain}/deliverables/download",
    tenant_route("export_files", "export", step_up=False),
)

DOMAIN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$"
MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
}


async def account_id_of(session: Any, domain: str) -> UUID:
    """The account of this tenant with that domain, or 404 (another tenant's looks the same)."""
    if not re.fullmatch(DOMAIN, domain):
        raise HTTPException(404, "not found")
    found = await session.scalar(text("SELECT id FROM accounts WHERE domain = :d"), {"d": domain})
    if found is None:
        raise HTTPException(404, "not found")
    return UUID(str(found))


@router.get("/{domain}/workspace", response_model=C.AccountWorkspace)
async def account_workspace(domain: str, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The Account workspace screen for one account."""
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        out = await W.workspace(s, account_id, ident.user_id, datetime.now(UTC))
    return {**out, "latest_run": None}


@router.get("/{domain}/deliverables", response_model=C.DeliverableFiles)
async def account_deliverables(domain: str, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The account's current deliverables (newest version of each)."""
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        return {"files": await W.deliverables(s, account_id)}


@router.get("/{domain}/deliverables/download")
async def download_deliverables(
    domain: str,
    request: Request,
    ids: str = Query(
        min_length=1, max_length=400, pattern=r"^(all|[0-9a-f-]{36}(,[0-9a-f-]{36}){0,9})$"
    ),
    ident: Identity = Depends(authorize),
) -> Response:
    """One file as itself, or several as one zip named after the account."""
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        current = {f["id"]: f for f in await W.deliverables(s, account_id)}
        wanted = list(current) if ids == "all" else ids.split(",")
        if not wanted or any(i not in current for i in wanted):
            raise HTTPException(404, "not found")
        rows = (
            await s.execute(
                text(
                    "SELECT id, filename, object_key, sha256 FROM deliverables WHERE id = ANY(:i)"
                ),
                {"i": [UUID(i) for i in wanted]},
            )
        ).all()
        for r in rows:
            try:
                require_owned(ident.tenant_id, r.object_key)
            except PathRefused:
                raise HTTPException(404, "not found") from None
        await audit_action(
            s,
            request,
            audit_class="export",
            action="download_deliverables",
            resource_type="account",
            resource_id=account_id,
        )
    store = request.app.state.container.payloads
    if len(rows) == 1:
        r = rows[0]
        body = await store.get(r.object_key)
        ext = r.filename.rsplit(".", 1)[-1]
        return Response(
            body,
            media_type=MEDIA.get(ext, "application/octet-stream"),
            headers={"Content-Disposition": f'attachment; filename="{r.filename}"'},
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for r in sorted(rows, key=lambda r: r.filename):
            archive.writestr(r.filename, await store.get(r.object_key))
    name = f"{domain.split('.')[0]}-files.zip"
    return Response(
        buffer.getvalue(),
        media_type=MEDIA["zip"],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
