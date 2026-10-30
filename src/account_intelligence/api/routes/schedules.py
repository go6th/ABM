"""Schedules: every account's refresh cadence, saved immediately, with the monthly credit estimate.

* ``GET /v1/schedules``: one row per account (cadence, next refresh, which cadences the client
  allows), the estimated credits a month at these settings and the pool.
* ``PUT /v1/schedules/{account_id}``: save one account's cadence (422 ``cadence_not_allowed``
  when it is faster than the client allows); returns the whole view so the estimate updates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import schedules as SC

router = APIRouter(tags=["schedules"])
register("GET", "/v1/schedules", tenant_route("read_public", "read", step_up=False))
register("PUT", "/v1/schedules/{account_id}", tenant_route("start_run", "run", step_up=False))


@router.get("/schedules", response_model=C.Schedules)
async def schedules(ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """Every account's schedule and the monthly estimate."""
    async with tenant_txn(ident.tenant_id) as s:
        return await SC.view(s, datetime.now(UTC))


class CadenceBody(BaseModel):
    """One account's new cadence."""

    model_config = ConfigDict(extra="forbid")
    cadence: Literal["3d", "weekly", "2w", "monthly", "off"]


@router.put("/schedules/{account_id}", response_model=C.Schedules)
async def save_cadence(
    account_id: UUID, body: CadenceBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Save the cadence now and return the updated view."""
    now = datetime.now(UTC)
    async with tenant_txn(ident.tenant_id) as s:
        found = await s.scalar(
            text("SELECT 1 FROM accounts WHERE id = :a AND deleted_at IS NULL"), {"a": account_id}
        )
        if not found:
            raise HTTPException(404, "not found")
        try:
            await SC.set_cadence(s, account_id, body.cadence, now)
        except SC.CadenceNotAllowed:
            settings_view = await SC.view(s, now)
            raise APIProblem(
                422,
                "cadence_not_allowed",
                "That schedule is not allowed",
                f"Your administrator allows refreshes {settings_view['fastest_label']} at most.",
            ) from None
        await audit_action(
            s,
            request,
            audit_class="run",
            action="set_schedule",
            resource_type="account",
            resource_id=account_id,
        )
        return await SC.view(s, now)
