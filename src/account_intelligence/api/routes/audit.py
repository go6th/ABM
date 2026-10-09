"""The tenant's audit trail (``/v1/audit``; AU1, AU2). Admins only.

* ``GET /v1/audit``: events newest first, filtered by actor, action prefix or resource, keyset
  paginated by ``before_seq`` (bounded page size). Reading the audit is itself audited.
* ``GET /v1/audit/export``: the client-safe CSV (``audit.export``), with a fresh step-up; the
  export is recorded in the same transaction that reads it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.db.session import tenant_txn
from account_intelligence.safety.client_output import ClientOutputLeak
from audit.events import load_policy
from audit.export import build_csv

router = APIRouter(prefix="/audit", tags=["audit"])
register("GET", "/v1/audit", tenant_route("read_audit", "read"))
register("GET", "/v1/audit/export", tenant_route("export_audit", "export"))

_COLUMNS = (
    "seq, at, actor, actor_kind, audit_class, action, resource_type, resource_id, route,"
    " request_id, outcome, support_grant_id"
)


@router.get("", response_model=C.AuditPage)
async def list_audit(
    request: Request,
    actor: str | None = Query(default=None, max_length=300),
    action: str | None = Query(default=None, max_length=60, pattern=r"^[a-z_.]+$"),
    resource_type: str | None = Query(default=None, max_length=40, pattern=r"^[a-z_]+$"),
    resource_id: str | None = Query(default=None, max_length=300),
    before_seq: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """One page of the tenant's audit events."""
    async with tenant_txn(ident.tenant_id) as s:
        rows = (
            await s.execute(
                text(
                    f"SELECT {_COLUMNS} FROM audit_events"  # nosec B608 -- fixed column list.
                    " WHERE (CAST(:actor AS text) IS NULL OR actor = :actor)"
                    " AND (CAST(:action AS text) IS NULL OR action LIKE :action || '%')"
                    " AND (CAST(:rtype AS text) IS NULL OR resource_type = :rtype)"
                    " AND (CAST(:rid AS text) IS NULL OR resource_id = :rid)"
                    " AND (CAST(:before AS bigint) IS NULL OR seq < :before)"
                    " ORDER BY seq DESC LIMIT :n"
                ),
                {
                    "actor": actor,
                    "action": action,
                    "rtype": resource_type,
                    "rid": resource_id,
                    "before": before_seq,
                    "n": limit,
                },
            )
        ).all()
    events = []
    for r in rows:
        item = dict(r._mapping)
        item["at"] = r.at.isoformat()
        item["support_grant_id"] = str(r.support_grant_id) if r.support_grant_id else None
        events.append(item)
    next_before = events[-1]["seq"] if len(events) == limit else None
    return {"events": events, "next_before_seq": next_before}


@router.get("/export")
async def export_audit(
    request: Request,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    ident: Identity = Depends(authorize),
) -> Response:
    """The client-safe CSV of the audit trail (categories, never providers, credits or cost)."""
    try:
        async with tenant_txn(ident.tenant_id) as s:
            body, rows = await build_csv(
                s, since=since, until=until, max_rows=load_policy().export_max_rows
            )
            await audit_action(
                s,
                request,
                audit_class="export",
                action="audit_csv",
                resource_type="audit",
                resource_id=f"rows-{rows}",
            )
    except ClientOutputLeak:
        raise HTTPException(500, "the export failed its client-output check") from None
    return Response(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
    )
