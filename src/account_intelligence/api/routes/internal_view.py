"""The Internal Only view of a run (two-output rule): never in a client file.

* ``GET /v1/runs/{run_id}/internal``: the file-derived findings with grade, citation (document
  and location) and the quoted passage; the documents read; and, for an enriched run, the linked
  public run's public findings in their own list labelled "Public research".
* ``GET /v1/runs/{run_id}/internal/file``: the Internal Only Word file.

Admins, analysts and sellers only (viewers never see internal material); every access is an
audit event (the guard's read event for the view, an export event for the file).

GUARANTEE(test_internal_view_is_role_gated_and_audited)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity, require_role
from account_intelligence.db.session import tenant_txn

router = APIRouter(prefix="/runs", tags=["runs"])
INTERNAL_ROLES = ("admin", "analyst", "seller")


@router.get("/{run_id}/internal", response_model=C.InternalView)
async def internal_view(
    run_id: UUID, request: Request, ident: Identity = Depends(require_role(*INTERNAL_ROLES))
) -> dict[str, Any]:
    """See the module docstring."""
    from documents import repo
    from documents.gate import INTERNAL_LABEL
    from documents.graph import findings
    from documents.internal_doc import as_dict

    async with tenant_txn(ident.tenant_id) as s:
        row = (
            await s.execute(
                text(
                    "SELECT r.input_mode, (SELECT c.id FROM account_runs c WHERE"
                    " c.run_input->>'linked_file_run' = r.id::text LIMIT 1) AS public_run,"
                    " EXISTS (SELECT 1 FROM internal_deliverables i WHERE i.run_id = r.id)"
                    " AS has_file"
                    " FROM account_runs r WHERE r.id = :r"
                ),
                {"r": run_id},
            )
        ).first()
        if row is None:
            raise HTTPException(404, "run not found")
        docs = await repo.run_documents(s, run_id)
        found = await findings(s, run_id)
        public: list[dict[str, Any]] = []
        if row.public_run is not None:
            public = [
                {
                    "attribute": f.attribute_key,
                    "value": f.value_text,
                    "grade": f.product_grade,
                    "source_label": "Public research",
                }
                for f in (
                    await s.execute(
                        text(
                            "SELECT attribute_key, value_text, product_grade FROM facts WHERE"
                            " run_id = :r AND status = 'current' AND visibility = 'public'"
                            " ORDER BY attribute_key, id LIMIT 500"
                        ),
                        {"r": row.public_run},
                    )
                ).all()
            ]
    return {
        "run_id": str(run_id),
        "label": INTERNAL_LABEL,
        "source_label": "From your files" if row.input_mode == "file" else "Public research",
        "documents": [
            {"document_id": str(d.id), "name": d.name, "passages": d.passages, "status": d.status}
            for d in docs
        ],
        "findings": [as_dict(f) for f in found],
        "public_run_id": str(row.public_run) if row.public_run else None,
        "public_findings": public,
        "file_available": bool(row.has_file),
    }


@router.get("/{run_id}/internal/file")
async def internal_file(
    run_id: UUID, request: Request, ident: Identity = Depends(require_role(*INTERNAL_ROLES))
) -> Response:
    """The Internal Only Word file (an export audit event)."""
    async with tenant_txn(ident.tenant_id) as s:
        key = (
            await s.execute(
                text("SELECT object_key FROM internal_deliverables WHERE run_id = :r"),
                {"r": run_id},
            )
        ).scalar_one_or_none()
        if key is None:
            raise HTTPException(404, "no internal file for this run")
        await audit_action(
            s,
            request,
            audit_class="export",
            action="internal_file",
            resource_type="run",
            resource_id=run_id,
        )
    data = await _internal_bytes(request, str(key))
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="internal-only-{run_id}.docx"',
            "Cache-Control": "no-store",
        },
    )


async def _internal_bytes(request: Request, key: str) -> bytes:
    """The file from where its run stored it: a file-mode run in document storage, a company
    research run in the payload store (both under the tenant's ``internal/`` prefix)."""
    container = request.app.state.container
    docs = getattr(container, "documents", None)
    if docs is not None:
        try:
            with docs.blobs.open(key) as reader:
                return bytes(reader.read())
        except (FileNotFoundError, KeyError, LookupError):
            pass
    payloads = getattr(container, "payloads", None)
    if payloads is None:
        raise HTTPException(503, "document storage is not configured")
    try:
        return bytes(await payloads.get(key))
    except Exception:
        raise HTTPException(404, "no internal file for this run") from None
