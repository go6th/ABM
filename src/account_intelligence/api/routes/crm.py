"""CRM write-back (Phase 4 WS7; ``/v1/crm``). Tenant admins only (opt-in, D-77).

* ``POST /v1/crm/{crm}/enable`` and ``/revoke``.
* ``POST /v1/crm/{crm}/mappings`` {domain, record_id}: a human-confirmed account mapping.
* ``POST /v1/crm/{crm}/preview`` {domain, kind}: the exact record that would be created from the
  account's delivered public findings (nothing stored).
* ``POST /v1/crm/{crm}/writes`` {domain, kind}: an idempotent write request.
* ``POST /v1/crm/{crm}/writes/approve`` {write_ids}: per-batch approval.
* ``GET /v1/crm/{crm}/writes``: every write and its events (tenant-visible audit).
Execution runs in a job with the configured client (``NotConfigured`` until an app is
registered); nothing here sends anything.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica.current import NEWEST_FACT
from crm_writeback import payloads
from crm_writeback import service as writeback

router = APIRouter(prefix="/crm", tags=["crm"])
Crm = Literal["salesforce", "hubspot"]
for _m, _p in (
    ("POST", "enable"),
    ("POST", "revoke"),
    ("POST", "mappings"),
    ("POST", "preview"),
    ("POST", "writes"),
    ("POST", "writes/approve"),
):
    register(_m, f"/v1/crm/{{crm}}/{_p}", tenant_route("manage_connectors", "admin"))
register("GET", "/v1/crm/{crm}/writes", tenant_route("manage_connectors", "read", step_up=False))


class Target(BaseModel):
    """An account and a record kind. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    domain: str = Field(max_length=253)
    kind: Literal["note", "task", "account_snapshot"] = "note"


class MappingBody(BaseModel):
    """A confirmed mapping."""

    model_config = ConfigDict(extra="forbid")
    domain: str = Field(max_length=253)
    record_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class Approval(BaseModel):
    """A batch to approve."""

    model_config = ConfigDict(extra="forbid")
    write_ids: list[UUID] = Field(min_length=1, max_length=25)


async def _account(s: Any, domain: str) -> UUID:
    row = (
        await s.execute(
            text("SELECT id FROM accounts WHERE domain = :d AND deleted_at IS NULL"), {"d": domain}
        )
    ).first()
    if row is None:
        raise HTTPException(404, "not found")
    return UUID(str(row.id))


async def _items(s: Any, account_id: UUID) -> list[payloads.Item]:
    """Public, current, Confirmed or Probable facts of the account's delivered output."""
    rows = (
        await s.execute(
            text(
                "SELECT f.attribute_key, f.visibility, f.value_json, min(src.source_code) AS code"
                " FROM facts f LEFT JOIN fact_evidence e ON e.fact_id = f.id"
                " LEFT JOIN sources src ON src.id = e.source_id WHERE f.account_id = :a"
                " AND f.superseded_at IS NULL AND f.status = 'current'"
                " AND f.product_grade IN ('confirmed', 'probable')" + NEWEST_FACT + " GROUP BY f.id"
                " ORDER BY f.created_at DESC LIMIT 10"
            ),
            {"a": account_id},
        )
    ).all()
    return [
        payloads.Item(
            str(r.value_json.get("text") or r.attribute_key),
            r.code or "S-0000",
            r.visibility,
            str(r.value_json.get("event_type") or ""),
            True,
        )
        for r in rows
    ]


def _refused(e: writeback.WriteRefused) -> APIProblem:
    return APIProblem(422, e.reason, "Write refused", "Nothing was written to your CRM.")


@router.post("/{crm}/enable", response_model=C.CrmToggle)
async def enable(
    crm: Crm, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Opt in."""
    async with tenant_txn(ident.tenant_id) as s:
        await writeback.enable(s, crm, actor=ident.user_id, now=plane_of(request).clock.now())
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="crm_enable",
            resource_type="crm",
            resource_id=crm,
        )
    return {"crm": crm, "enabled": True}


@router.post("/{crm}/revoke", response_model=C.CrmToggle)
async def revoke(
    crm: Crm, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Stop writes at once."""
    async with tenant_txn(ident.tenant_id) as s:
        await writeback.revoke(s, crm, actor=ident.user_id, now=plane_of(request).clock.now())
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="crm_revoke",
            resource_type="crm",
            resource_id=crm,
        )
    return {"crm": crm, "enabled": False}


@router.post("/{crm}/mappings", response_model=C.CrmMapping, status_code=201)
async def confirm(
    crm: Crm, body: MappingBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Confirm an account's CRM record."""
    async with tenant_txn(ident.tenant_id) as s:
        acc = await _account(s, body.domain)
        await writeback.confirm_mapping(
            s,
            account_id=acc,
            crm=crm,
            record_id=body.record_id,
            actor=ident.user_id,
            now=plane_of(request).clock.now(),
        )
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="crm_mapping",
            resource_type="account",
            resource_id=body.domain,
        )
    return {"domain": body.domain, "record_id": body.record_id}


async def _draft(s: Any, crm: str, body: Target) -> tuple[UUID, Any]:
    acc = await _account(s, body.domain)
    confirmed = await writeback.confirmed_record(s, acc, crm)
    try:
        draft = writeback.preview(
            body.kind,
            candidates=[confirmed] if confirmed else [],
            confirmed=confirmed,
            account_label=body.domain,
            items=await _items(s, acc),
        )
    except writeback.WriteRefused as e:
        raise _refused(e) from None
    return acc, draft


@router.post("/{crm}/preview", response_model=C.CrmPreview)
async def preview(
    crm: Crm, body: Target, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Exactly what would be created."""
    async with tenant_txn(ident.tenant_id) as s:
        _, draft = await _draft(s, crm, body)
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="crm_preview",
            resource_type="account",
            resource_id=body.domain,
        )
    return {
        "kind": draft.kind,
        "record_id": draft.account_record_id,
        "title": draft.title,
        "body": draft.body,
    }


@router.post("/{crm}/writes", response_model=C.CrmWriteRequested, status_code=201)
async def request_write(
    crm: Crm, body: Target, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """An idempotent write request (approval follows)."""
    async with tenant_txn(ident.tenant_id) as s:
        if not await writeback.enabled(s, crm):
            raise APIProblem(409, "crm_not_enabled", "Not enabled", "Enable the CRM first.")
        acc, draft = await _draft(s, crm, body)
        req = await writeback.request(
            s,
            account_id=acc,
            crm=crm,
            draft=draft,
            actor=ident.user_id,
            now=plane_of(request).clock.now(),
        )
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="crm_request",
            resource_type="crm_write",
            resource_id=req.write_id,
        )
    return {"write_id": str(req.write_id), "new": req.new}


@router.post("/{crm}/writes/approve", response_model=C.CrmApproved)
async def approve(
    crm: Crm, body: Approval, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Approve a batch."""
    async with tenant_txn(ident.tenant_id) as s:
        known = {
            str(r.id)
            for r in (
                await s.execute(
                    text("SELECT id FROM crm_writes WHERE crm = :c AND id = ANY(:ids)"),
                    {"c": crm, "ids": body.write_ids},
                )
            ).all()
        }
        if known != {str(w) for w in body.write_ids}:
            raise HTTPException(404, "not found")
        try:
            n = await writeback.approve(
                s, body.write_ids, actor=ident.user_id, now=plane_of(request).clock.now()
            )
        except writeback.WriteRefused as e:
            raise _refused(e) from None
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="crm_approve",
            resource_type="crm",
            resource_id=crm,
        )
    return {"approved": n}


@router.get("/{crm}/writes", response_model=C.CrmWrites)
async def writes(
    crm: Crm, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Every write and its events."""
    async with tenant_txn(ident.tenant_id) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT w.id, w.kind, w.title, w.created_at, array_agg(e.event ORDER BY e.id)"
                    " AS ev"
                    " FROM crm_writes w LEFT JOIN crm_write_events e ON e.write_id = w.id"
                    " WHERE w.crm = :c GROUP BY w.id ORDER BY w.created_at DESC LIMIT 200"
                ),
                {"c": crm},
            )
        ).all()
    return {
        "writes": [
            {
                "id": str(r.id),
                "kind": r.kind,
                "title": r.title,
                "events": list(r.ev or []),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }
