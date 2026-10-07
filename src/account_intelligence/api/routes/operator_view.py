"""Support view-as (``/operator/v1/view``; AD3, D-66): read-only views of ONE tenant through an
active support grant. The tenant comes from the grant; every call is a support event and a tenant
audit row (``api.operator.require_operator``). Responses carry ``X-Support-View: read-only`` so
the console shows its banner. There is no write, export, spend, cap, key or user route here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from account_intelligence.api.operator import OperatorIdentity, require_operator
from account_intelligence.api.policy import operator_route, register
from account_intelligence.db.session import tenant_txn
from tenancy import settings

router = APIRouter(prefix="/operator/v1/view", tags=["support"])
register("GET", "/operator/v1/view/accounts", operator_route("read"))
register("GET", "/operator/v1/view/settings", operator_route("read"))
BANNER = {"X-Support-View": "read-only", "Cache-Control": "no-store"}


@router.get("/accounts")
async def accounts(
    request: Request, op: OperatorIdentity = Depends(require_operator)
) -> JSONResponse:
    """The tenant's accounts and latest run states, as the tenant sees them."""
    async with tenant_txn(op.tenant_id) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT a.domain, r.status FROM accounts a LEFT JOIN LATERAL"
                    " (SELECT status FROM account_runs WHERE account_id = a.id"
                    " ORDER BY created_at DESC LIMIT 1) r ON true"
                    " ORDER BY a.domain LIMIT 200"
                )
            )
        ).all()
    body: dict[str, Any] = {
        "support_grant": op.support_session_id,
        "accounts": [{"domain": r.domain, "latest_run_status": r.status} for r in rows],
    }
    return JSONResponse(body, headers=BANNER)


@router.get("/settings")
async def tenant_settings(
    request: Request, op: OperatorIdentity = Depends(require_operator)
) -> JSONResponse:
    """The tenant's current settings, as the tenant sees them."""
    async with tenant_txn(op.tenant_id) as s:
        try:
            current = await settings.current(s)
            body: dict[str, Any] = {
                "version": current.version,
                "settings": current.settings.model_dump(mode="json"),
            }
        except settings.SettingsInvalid:
            body = {"version": None, "settings": None, "reason": "settings_invalid"}
    return JSONResponse({"support_grant": op.support_session_id, **body}, headers=BANNER)
