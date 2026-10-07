"""Exports (``/v1/exports``; VW1, VW5, TN4).

* ``POST /v1/exports`` {domain, run_id?, kind, table?}: build a package from the run's rows:
  ``files`` (the standing Word and Excel set and the workspace zip), ``csv`` (one table) or
  ``pdf`` (the Account Summary). The package is checked for parity against the materialised
  rows the tables show, scanned, and recorded as ``delivered`` or ``blocked`` with a reason, in
  the same transaction as its audit event.
* ``GET /v1/exports/{package_id}``: the package (files, parity verdicts; never cell content).
* ``GET /v1/exports/{package_id}/files/{index}``: download one file of a DELIVERED package. The
  file's object key must belong to the session's tenant (``tenancy.storage.require_owned``). No
  token travels in a URL: the session authorises the download and the download is audited.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.routes.accounts import (
    DOMAIN,
    exclusion_rule,
    materialized,
    resolve_run,
)
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.ports import NotConfigured
from metering import events as usage
from pilot import usefulness
from pilot.feedback import policy as pilot_policy
from tenancy.storage import PathRefused, require_owned, tenant_of_key
from views import packages
from views.materialize import api_rows
from views.tables import TABLES

router = APIRouter(prefix="/exports", tags=["exports"])
register("POST", "/v1/exports", tenant_route("export_files", "export"))
register("GET", "/v1/exports/{package_id}", tenant_route("export_files", "read"))
register("GET", "/v1/exports/{package_id}/files/{index}", tenant_route("export_files", "export"))
_MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
    "csv": "text/csv",
    "pdf": "application/pdf",
}


class ExportBody(BaseModel):
    """What to export. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    domain: str = Field(pattern=DOMAIN, max_length=253)
    run_id: UUID | None = None
    kind: Literal["files", "csv", "pdf"] = "files"
    table: Literal["people", "jobs", "tech", "facts", "coverage"] | None = None


async def _deliverables(request: Request, tenant_id: UUID, run_id: UUID) -> list[Any]:
    builder = getattr(request.app.state.container, "builder", None)
    if builder is None:
        raise NotConfigured("the export builders are not configured")
    result = await builder.build_all(str(tenant_id), str(run_id))
    if result.status != "complete":
        return []
    payloads = request.app.state.container.payloads
    return [
        (d.kind, d.filename, d.object_key, await payloads.get(d.object_key))
        for d in result.deliverables
    ]


@router.post("", status_code=201)
async def create_export(
    body: ExportBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Build, check and record one package."""
    if body.kind == "csv" and body.table is None:
        raise APIProblem(422, "table_required", "Table required", "A CSV export needs a table.")
    async with tenant_txn(ident.tenant_id) as s:
        rule = await exclusion_rule(s)
        account_id, run_id = await resolve_run(s, body.domain, body.run_id, rule)
    mat = await materialized(request, ident.tenant_id, run_id)
    async with tenant_txn(ident.tenant_id) as s:
        api: dict[str, list[dict[str, Any]]] = {
            str(t): await api_rows(s, run_id, t) for t in TABLES
        }
    plane = plane_of(request)
    csv_body: bytes | None = None
    if mat.status != "ready":
        pkg = packages.blocked(body.kind, "tables_withheld")
    elif body.kind == "csv":
        assert body.table is not None
        pkg, csv_body = packages.csv_package(ident.tenant_id, run_id, body.table, api[body.table])
    else:
        produced = await _deliverables(request, ident.tenant_id, run_id)
        if not produced:
            pkg = packages.blocked(body.kind, "build_failed")
        elif body.kind == "files":
            pkg = packages.files_package(produced, api)
        else:
            builder = request.app.state.container.builder
            converter = getattr(builder, "pdf", None)
            summary = next((b for k, _, _, b in produced if k == "summary"), None)
            pdf = converter.pdf_bytes(summary) if converter and summary else None
            text_ = converter.text_of_pdf(pdf) if converter and pdf else None
            pkg = packages.pdf_package(ident.tenant_id, run_id, pdf, text_, api)
            if pkg.status == "delivered" and pdf is not None:
                await plane.objects.put_if_absent(pkg.files[0].key, pdf, "application/pdf")
    if csv_body is not None and pkg.status == "delivered":
        await plane.objects.put_if_absent(pkg.files[0].key, csv_body, "text/csv")
    async with tenant_txn(ident.tenant_id) as s:
        package_id = await packages.record(
            s,
            pkg,
            run_id=run_id,
            account_id=account_id,
            actor=ident.user_id,
            now=plane.clock.now(),
            rows_hash=mat.rows_hash,
        )
        if pkg.status == "delivered":
            await usage.record(s, "export_created", package_id, plane.clock.now())
        await audit_action(
            s,
            request,
            audit_class="export",
            action=f"package_{pkg.kind}",
            resource_type="export_package",
            resource_id=package_id,
            outcome="ok" if pkg.status == "delivered" else "denied",
        )
    return {
        "package_id": str(package_id),
        "run_id": str(run_id),
        "kind": pkg.kind,
        "status": pkg.status,
        "reason": pkg.reason,
        "parity": pkg.parity_json(),
        "files": [
            {"index": i, "kind": f.kind, "name": f.name, "sha256": f.sha256, "size": f.size}
            for i, f in enumerate(pkg.files)
        ],
    }


async def _package(s: Any, package_id: UUID) -> Any:
    row = (
        await s.execute(
            text(
                "SELECT id, run_id, kind, status, reason, files, parity, created_at"
                " FROM export_packages WHERE id = :i"
            ),
            {"i": package_id},
        )
    ).first()
    if row is None:
        raise HTTPException(404, "not found")
    return row


@router.get("/{package_id}")
async def read_export(
    package_id: UUID, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """One package of the caller's tenant."""
    async with tenant_txn(ident.tenant_id) as s:
        row = await _package(s, package_id)
    return {
        "package_id": str(row.id),
        "run_id": str(row.run_id),
        "kind": row.kind,
        "status": row.status,
        "reason": row.reason,
        "parity": row.parity,
        "files": [
            {
                "index": i,
                "kind": f["kind"],
                "name": f["name"],
                "sha256": f["sha256"],
                "size": f["size"],
            }
            for i, f in enumerate(row.files)
        ],
        "created_at": row.created_at.isoformat(),
    }


_FACT_FILES = frozenset({"summary", "evidence", "workspace", "facts", "pdf"})


def _carries_facts(package_kind: str, file: Any) -> bool:
    """Whether a downloaded file holds the run's graded facts (D-68: a Confirmed fact in a
    downloaded export counts as referenced); the People, Jobs and Tech files do not."""
    return package_kind == "pdf" or file.get("kind") in _FACT_FILES


@router.get("/{package_id}/files/{index}")
async def download(
    package_id: UUID,
    request: Request,
    index: int = Path(ge=0, le=50),
    ident: Identity = Depends(authorize),
) -> Response:
    """One file of a delivered package (the object must be the session tenant's)."""
    async with tenant_txn(ident.tenant_id) as s:
        row = await _package(s, package_id)
        if row.status != "delivered":
            raise APIProblem(
                409,
                "package_blocked",
                "Package not delivered",
                "This package did not pass its checks and cannot be downloaded.",
            )
        if index >= len(row.files):
            raise HTTPException(404, "not found")
        f = row.files[index]
        try:
            require_owned(ident.tenant_id, f["key"])
        except PathRefused:
            raise HTTPException(404, "not found") from None
        await audit_action(
            s,
            request,
            audit_class="export",
            action="download",
            resource_type="export_file",
            resource_id=f["sha256"],
        )
        if _carries_facts(row.kind, f):
            await usefulness.exported(
                s,
                run_id=row.run_id,
                user_id=ident.user_id,
                now=plane_of(request).clock.now(),
                limit=int(pilot_policy()["max_export_references"]),
            )
    if (
        f["store"] == "deliverables"
        or tenant_of_key(f["key"])
        and not f["key"].startswith("tenants/")
    ):
        body = await request.app.state.container.payloads.get(f["key"])
    else:
        body = await plane_of(request).objects.get(f["key"])
    ext = f["name"].rsplit(".", 1)[-1]
    return Response(
        body,
        media_type=_MEDIA.get(ext, "application/octet-stream"),
        headers={
            "Content-Disposition": f'attachment; filename="{f["name"]}"',
            "X-Content-SHA256": f["sha256"],
        },
    )
