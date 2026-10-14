"""Limits per user (owner decision 2026-10-14, Phase 4B section 5).

* ``GET /v1/me/limits``: researches and chats used, the limit and what is left this period, when
  they reset, and which requests for more are open.
* ``POST /v1/limits/requests``: "Request more from admin": an in-app request (the user, the
  client, which limit, an optional note) for the 6th admin console. Nothing is sent outside the
  platform; a second request for the same limit while one is open returns the open one.

A refused action answers ``403 limit_reached`` with the words the user sees ("You have used your
2 researches. Ask your admin to increase your limit.") and which limit it was.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audited_txn
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import limits as LIM

router = APIRouter(tags=["limits"])
register("GET", "/v1/me/limits", tenant_route("read_public", "read", step_up=False))
register("POST", "/v1/limits/requests", tenant_route("give_feedback", "admin", step_up=False))


def limit_reached(reached: LIM.LimitReached) -> APIProblem:
    """The refusal of an action whose limit is used up."""
    return APIProblem(
        403,
        "limit_reached",
        "Limit reached",
        reached.message,
        extra={"limit": reached.kind, "allowed": reached.limit},
    )


class MoreBody(BaseModel):
    """Which limit to raise, and an optional note for the admin."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["researches", "chats"]
    note: str | None = Field(default=None, max_length=500)


@router.get("/me/limits", response_model=C.MyLimits)
async def my_limits(ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The caller's limits and use this period."""
    async with tenant_txn(ident.tenant_id) as s:
        return await LIM.view(s, ident.user_id, datetime.now(UTC))


@router.post("/limits/requests", response_model=C.LimitRequestSent, status_code=201)
async def request_more(
    body: MoreBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """File a request for more researches or chats."""
    async with audited_txn(
        request,
        audit_class="admin",
        action="limit_request",
        resource_type="limit_request",
        resource_id=None,
    )(ident.tenant_id) as s:
        rid, created = await LIM.request_more(
            s, ident.user_id, body.kind, body.note, datetime.now(UTC)
        )
    return {"request_id": rid, "kind": body.kind, "status": "pending", "created": created}
