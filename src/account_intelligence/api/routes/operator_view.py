"""Support view-as (``/operator/v1/view``; AD3, D-66): read-only views of ONE tenant through an
active support grant. ``/workspace`` is the admin console's View as a client: the client's name,
when the grant ends, its users and credits, and its accounts with names, lanes, schedule and
counts; never question text, answers or file contents. The tenant comes from the grant; every
call is a support event and a tenant audit row (``api.operator.require_operator``). Responses
carry ``X-Support-View: read-only`` so the console shows its banner. There is no write, export,
spend, cap, key or user route here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.operator import OperatorIdentity, require_operator
from account_intelligence.api.policy import operator_route, register
from account_intelligence.db.session import tenant_txn
from tenancy import settings

router = APIRouter(prefix="/operator/v1/view", tags=["support"])
register("GET", "/operator/v1/view/accounts", operator_route("read"))
register("GET", "/operator/v1/view/settings", operator_route("read"))
register("GET", "/operator/v1/view/workspace", operator_route("read"))
BANNER = {"X-Support-View": "read-only", "Cache-Control": "no-store"}


@router.get("/accounts", responses={200: {"model": C.ViewAsAccounts}})
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
    return JSONResponse(
        C.ViewAsAccounts.model_validate(body).model_dump(mode="json"), headers=BANNER
    )


@router.get("/settings", responses={200: {"model": C.ViewAsSettings}})
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
    checked = C.ViewAsSettings.model_validate({"support_grant": op.support_session_id, **body})
    return JSONResponse(checked.model_dump(mode="json", exclude_unset=True), headers=BANNER)


@router.get("/workspace", responses={200: {"model": C.ViewAsWorkspace}})
async def workspace(
    request: Request, op: OperatorIdentity = Depends(require_operator)
) -> JSONResponse:
    """View as a client: the workspace as the client sees its accounts (names and counts)."""
    from account_intelligence.api.tenant_plane import plane_of
    from account_intelligence.replica.credits import pool
    from account_intelligence.replica.settings import CADENCE_LABEL

    async with tenant_txn(op.tenant_id) as s:
        name = await s.scalar(text("SELECT name FROM tenants WHERE id = :t"), {"t": op.tenant_id})
        expires = await s.scalar(
            text("SELECT expires_at FROM support_grants WHERE id = :g"),
            {"g": op.support_session_id},
        )
        users = await s.scalar(text("SELECT count(*) FROM memberships WHERE status = 'active'"))
        credits = await pool(s, plane_of(request).clock.now())
        rows = (
            await s.execute(
                text(
                    "SELECT coalesce(a.name, a.domain) AS name, a.domain, a.lanes,"
                    " a.refresh_cadence,"
                    " (SELECT max(r.finished_at) FROM account_runs r WHERE r.account_id = a.id"
                    "   AND r.status IN ('COMPLETED', 'PARTIAL')) AS researched,"
                    " (SELECT count(*) FROM people p WHERE p.account_id = a.id AND NOT p.excluded"
                    "   AND p.merged_into IS NULL AND p.erased_at IS NULL"
                    "   AND NOT coalesce(p.internal_only, false)) AS people,"
                    " (SELECT count(*) FROM signal_events g WHERE g.account_id = a.id) AS signals,"
                    " (SELECT count(*) FROM sources o WHERE o.account_id = a.id) AS sources"
                    " FROM accounts a ORDER BY coalesce(a.name, a.domain) LIMIT 500"
                )
            )
        ).all()
    body: dict[str, Any] = {
        "support_grant": op.support_session_id,
        "client": str(name),
        "expires_at": expires.isoformat() if expires else "",
        "users": int(users or 0),
        "credits_used": int(credits["used"]),
        "credit_pool": int(credits["pool"]),
        "accounts": [
            {
                "name": str(r.name),
                "domain": str(r.domain),
                "lanes": list(r.lanes or []),
                "schedule": CADENCE_LABEL.get(str(r.refresh_cadence or "off"), "Off"),
                "last_researched_at": r.researched.isoformat() if r.researched else None,
                "people": int(r.people),
                "signals": int(r.signals),
                "sources": int(r.sources),
            }
            for r in rows
        ],
    }
    return JSONResponse(
        C.ViewAsWorkspace.model_validate(body).model_dump(mode="json"), headers=BANNER
    )
