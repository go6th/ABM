"""Plan usage (``/v1/usage``; MT1 to MT3, D-63). Admins only; units and percentages, never cost,
credits or provider names.

* ``GET /v1/usage``: the current period's events by kind and each allowance with its share used.
* ``GET /v1/usage/export``: the period summary as CSV (recomputed from the events).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from metering.usage_summary import summary, summary_csv

router = APIRouter(prefix="/usage", tags=["usage"])
register("GET", "/v1/usage", tenant_route("read_usage", "read"))
register("GET", "/v1/usage/export", tenant_route("read_usage", "export"))


def _period(value: str | None) -> str | None:
    if value is not None and not re.fullmatch(r"\d{4}-\d{2}", value):
        raise APIProblem(422, "bad_period", "Bad period", "Periods are written YYYY-MM.")
    return value


@router.get("", response_model=C.Usage)
async def usage(
    request: Request,
    period: str | None = Query(default=None, max_length=7),
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """The period's usage in units."""
    async with tenant_txn(ident.tenant_id) as s:
        return await summary(s, plane_of(request).clock.now(), _period(period))


@router.get("/export")
async def usage_export(
    request: Request,
    period: str | None = Query(default=None, max_length=7),
    ident: Identity = Depends(authorize),
) -> Response:
    """The period summary as CSV (billing-ready, recomputable from the events)."""
    async with tenant_txn(ident.tenant_id) as s:
        body = summary_csv(await summary(s, plane_of(request).clock.now(), _period(period)))
        await audit_action(
            s,
            request,
            audit_class="export",
            action="usage_csv",
            resource_type="usage",
            resource_id=period or "current",
        )
    return Response(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="usage.csv"'},
    )
