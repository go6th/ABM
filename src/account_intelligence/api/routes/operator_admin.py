"""Operator platform routes (``/operator/v1``; AD1, AD2, AD4). 6th staff only; never tenant
content.

* ``GET /operator/v1/tenants``: identifiers, plan, suspension and counts of every tenant.
* ``POST /operator/v1/tenants``: provision a tenant (empty wallet).
* ``POST /operator/v1/tenants/{tenant_id}/suspend`` and ``/reactivate`` {reason_code}.
* ``POST /operator/v1/tenants/{tenant_id}/quotas`` and ``/plan``: insert-only versions.
* Deletion on request (two people): ``/deletion`` (request), ``/deletion/{request_id}/dry-run``,
  ``/approve`` (a different operator), ``/execute`` (bounded batches, resumable).
* ``GET``/``POST /operator/v1/flags``: flag versions; ``GET``/``POST /operator/v1/keys``: broker key
  REFERENCE versions (write-only: the reference is never returned).
Every action is an ``operator_audit`` row; a tenant lifecycle change is also in that tenant's
audit (customer-visible).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.api import contract as C
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.operator import StaffIdentity, require_staff
from account_intelligence.api.policy import OperatorRole, operator_route, register
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn, untenanted_session
from admin import flags, keys, quotas, tenants
from audit.events import AuditEvent, record
from identity.sessions import load_policy, step_up_fresh
from tenancy import lifecycle

router = APIRouter(prefix="/operator/v1", tags=["operator"])
OPS: frozenset[OperatorRole] = frozenset({"operator"})
STAFF: frozenset[OperatorRole] = frozenset({"operator", "support"})
register("GET", "/operator/v1/session", operator_route("read", tenant="none", roles=STAFF))
register("GET", "/operator/v1/tenants", operator_route("read", tenant="none", roles=STAFF))
register("POST", "/operator/v1/tenants", operator_route("admin", tenant="none", roles=OPS))
for _p in (
    "suspend",
    "reactivate",
    "quotas",
    "plan",
    "deletion",
    "deletion/{request_id}/dry-run",
    "deletion/{request_id}/approve",
    "deletion/{request_id}/execute",
):
    register(
        "POST",
        f"/operator/v1/tenants/{{tenant_id}}/{_p}",
        operator_route("admin", tenant="operator_target", roles=OPS),
    )
register("GET", "/operator/v1/flags", operator_route("read", tenant="none", roles=OPS))
register("POST", "/operator/v1/flags", operator_route("admin", tenant="none", roles=OPS))
register("GET", "/operator/v1/keys", operator_route("read", tenant="none", roles=OPS))
register("POST", "/operator/v1/keys", operator_route("admin", tenant="none", roles=OPS))


class Reason(BaseModel):
    """A reason code (identifiers only)."""

    model_config = ConfigDict(extra="forbid")
    reason_code: str = Field(pattern=r"^[a-z_]{1,40}$")


@router.get("/session", response_model=C.OperatorSession)
async def operator_session(
    request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """Return only the verified staff principal, role and MFA freshness."""
    return {
        "staff_id": who.staff_id,
        "role": who.role,
        "mfa_fresh": step_up_fresh(
            who.step_up_at, plane_of(request).clock.now(), load_policy()
        ),
    }


async def _op_audit(
    request: Request, who: StaffIdentity, action: str, target: str | None, reason: str | None
) -> None:
    async with untenanted_session() as plain:
        await tenants.audit(
            plain,
            operator=who.staff_id,
            action=action,
            target=target,
            reason_code=reason,
            request_id=getattr(request.state, "request_id", None),
            now=plane_of(request).clock.now(),
        )


async def _tenant_audit(
    s: Any, request: Request, who: StaffIdentity, action: str, resource: str
) -> None:
    """The customer-visible entry of an operator lifecycle or quota action."""
    await record(
        s,
        AuditEvent(
            at=plane_of(request).clock.now(),
            actor=who.staff_id,
            actor_kind="operator",
            audit_class="admin",
            action=f"operator.{action}",
            resource_type="tenant",
            resource_id=resource,
            route=None,
            request_id=getattr(request.state, "request_id", None),
        ),
    )


@router.get("/tenants", response_model=C.TenantsOverview)
async def overview(request: Request, who: StaffIdentity = Depends(require_staff)) -> dict[str, Any]:
    """Every tenant's metadata (no names, no content)."""
    async with untenanted_session() as plain:
        return {"tenants": await tenants.overview(plain)}


class NewTenant(BaseModel):
    """A tenant to provision."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


@router.post("/tenants", response_model=C.TenantCreated, status_code=201)
async def provision(
    body: NewTenant, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """Provision a tenant."""
    async with untenanted_session() as plain:
        tid = await tenants.create(plain, name=body.name)
    async with tenant_txn(tid) as s:
        st = await lifecycle.state(s)
        await lifecycle.append(
            s,
            st,
            "created",
            actor=who.staff_id,
            plane="operator",
            now=plane_of(request).clock.now(),
        )
    await _op_audit(request, who, "tenant.create", str(tid), None)
    return {"tenant_id": str(tid)}


async def _lifecycle(
    request: Request, who: StaffIdentity, tenant_id: UUID, action: str, body: Reason
) -> dict[str, Any]:
    now = plane_of(request).clock.now()
    try:
        async with tenant_txn(tenant_id) as s:
            if action == "suspend":
                await lifecycle.suspend(
                    s, actor=who.staff_id, plane="operator", reason_code=body.reason_code, now=now
                )
            else:
                await lifecycle.reactivate(
                    s, actor=who.staff_id, plane="operator", reason_code=body.reason_code, now=now
                )
            await _tenant_audit(s, request, who, action, str(tenant_id))
    except lifecycle.LifecycleError as e:
        raise APIProblem(409, "lifecycle_refused", "Refused", str(e)) from None
    await _op_audit(request, who, f"tenant.{action}", str(tenant_id), body.reason_code)
    return {"tenant_id": str(tenant_id), "status": "suspended" if action == "suspend" else "active"}


@router.post("/tenants/{tenant_id}/suspend", response_model=C.TenantLifecycle)
async def suspend(
    tenant_id: UUID, body: Reason, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """Suspend a tenant (sessions and runs stop within the membership-cache bound)."""
    return await _lifecycle(request, who, tenant_id, "suspend", body)


@router.post("/tenants/{tenant_id}/reactivate", response_model=C.TenantLifecycle)
async def reactivate(
    tenant_id: UUID, body: Reason, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """Lift a suspension."""
    return await _lifecycle(request, who, tenant_id, "reactivate", body)


class QuotaBody(Reason):
    """One quota override version."""

    dimension: Literal[
        "accounts", "runs_per_period", "refreshes_per_period", "seats", "connectors", "storage_mb"
    ]
    limit_value: int = Field(ge=0, le=1_000_000)


@router.post("/tenants/{tenant_id}/quotas", response_model=C.QuotaSet, status_code=201)
async def set_quota(
    tenant_id: UUID, body: QuotaBody, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """A new quota version for one tenant."""
    async with tenant_txn(tenant_id) as s:
        version = await quotas.set_quota(
            s,
            dimension=body.dimension,
            limit_value=body.limit_value,
            set_by=who.staff_id,
            reason_code=body.reason_code,
            now=plane_of(request).clock.now(),
        )
        await _tenant_audit(s, request, who, "quota", f"{body.dimension}:{version}")
    await _op_audit(request, who, "tenant.quota", str(tenant_id), body.reason_code)
    return {"dimension": body.dimension, "version": version}


class PlanBody(Reason):
    """A plan assignment."""

    plan_code: str = Field(pattern=r"^[a-z_]{1,40}$")


@router.post("/tenants/{tenant_id}/plan", response_model=C.PlanSet, status_code=201)
async def set_plan(
    tenant_id: UUID, body: PlanBody, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """A new plan assignment version for one tenant."""
    try:
        async with tenant_txn(tenant_id) as s:
            version = await quotas.assign_plan(
                s,
                plan_code=body.plan_code,
                assigned_by=who.staff_id,
                reason_code=body.reason_code,
                now=plane_of(request).clock.now(),
            )
            await _tenant_audit(s, request, who, "plan", f"{body.plan_code}:{version}")
    except ValueError:
        raise HTTPException(422, "unknown plan") from None
    await _op_audit(request, who, "tenant.plan", str(tenant_id), body.reason_code)
    return {"plan_code": body.plan_code, "version": version}


@router.post("/tenants/{tenant_id}/deletion", response_model=C.DeletionRequested, status_code=201)
async def request_deletion(
    tenant_id: UUID, body: Reason, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """Open a deletion request (dry run and a second operator come next)."""
    try:
        async with tenant_txn(tenant_id) as s:
            rid = await lifecycle.request_deletion(
                s,
                actor=who.staff_id,
                plane="operator",
                reason_code=body.reason_code,
                now=plane_of(request).clock.now(),
            )
            await _tenant_audit(s, request, who, "deletion_requested", rid)
    except lifecycle.LifecycleError as e:
        raise APIProblem(409, "lifecycle_refused", "Refused", str(e)) from None
    await _op_audit(request, who, "tenant.deletion_request", str(tenant_id), body.reason_code)
    return {"request_id": rid}


async def _deletion_step(
    request: Request, who: StaffIdentity, tenant_id: UUID, request_id: str, step: str
) -> dict[str, Any]:
    now = plane_of(request).clock.now()
    try:
        async with tenant_txn(tenant_id) as s:
            if step == "dry_run":
                out: dict[str, Any] = await lifecycle.dry_run(
                    s, request_id, actor=who.staff_id, now=now
                )
            elif step == "approve":
                await lifecycle.approve(s, request_id, actor=who.staff_id, now=now)
                out = {"request_id": request_id, "approved_by": who.staff_id}
            else:
                result = await lifecycle.execute(s, request_id, actor=who.staff_id, now=now)
                out = {
                    "request_id": request_id,
                    "erased": result.erased,
                    "exit_reason": result.exit_reason,
                }
            await _tenant_audit(s, request, who, f"deletion_{step}", request_id)
    except lifecycle.LifecycleError as e:
        raise APIProblem(409, "lifecycle_refused", "Refused", str(e)) from None
    await _op_audit(request, who, f"tenant.deletion_{step}", str(tenant_id), None)
    return out


@router.post("/tenants/{tenant_id}/deletion/{request_id}/dry-run", response_model=C.DeletionStep)
async def deletion_dry_run(
    tenant_id: UUID, request_id: UUID, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """Count exactly what would be removed and what is blocked."""
    return await _deletion_step(request, who, tenant_id, str(request_id), "dry_run")


@router.post("/tenants/{tenant_id}/deletion/{request_id}/approve", response_model=C.DeletionStep)
async def deletion_approve(
    tenant_id: UUID, request_id: UUID, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """The second person's approval."""
    return await _deletion_step(request, who, tenant_id, str(request_id), "approve")


@router.post("/tenants/{tenant_id}/deletion/{request_id}/execute", response_model=C.DeletionStep)
async def deletion_execute(
    tenant_id: UUID, request_id: UUID, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """One bounded, resumable pass."""
    return await _deletion_step(request, who, tenant_id, str(request_id), "execute")


class FlagBody(Reason):
    """A flag rule version."""

    flag: str = Field(pattern=r"^[a-z_]{1,60}$")
    rule: dict[str, Any]


@router.get("/flags", response_model=C.Flags)
async def list_flags(
    request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """Every flag's newest rule."""
    from sqlalchemy import text

    async with untenanted_session() as plain:
        rows = (
            await plain.execute(
                text(
                    "SELECT DISTINCT ON (flag) flag, version, rule FROM feature_flags"
                    " ORDER BY flag, version DESC"
                )
            )
        ).all()
    return {"flags": [{"flag": r.flag, "version": r.version, "rule": r.rule} for r in rows]}


@router.post("/flags", response_model=C.FlagSet, status_code=201)
async def set_flag(
    body: FlagBody, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """A new flag version (with its change event)."""
    try:
        async with untenanted_session() as plain:
            version = await flags.set_rule(
                plain,
                flag=body.flag,
                rule=body.rule,
                actor=who.staff_id,
                reason_code=body.reason_code,
                now=plane_of(request).clock.now(),
            )
    except ValueError:
        raise HTTPException(422, "bad flag rule") from None
    await _op_audit(request, who, "flag.set", body.flag, body.reason_code)
    return {"flag": body.flag, "version": version}


class KeyBody(Reason):
    """A broker key REFERENCE (the key itself never travels through this API)."""

    provider_code: str = Field(pattern=r"^p_[a-z]+$")
    secret_ref: str = Field(pattern=r"^[A-Za-z0-9/_.:-]{1,255}$")


@router.get("/keys", response_model=C.KeyVersions)
async def list_keys(
    request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """Key versions WITHOUT their references (write-only)."""
    async with untenanted_session() as plain:
        return {"keys": await keys.versions(plain)}


@router.post("/keys", response_model=C.KeyAdded, status_code=201)
async def add_key(
    body: KeyBody, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """A new key reference version (takes effect for runs created after it)."""
    async with untenanted_session() as plain:
        version = await keys.add_version(
            plain,
            provider_code=body.provider_code,
            secret_ref=body.secret_ref,
            actor=who.staff_id,
            reason_code=body.reason_code,
            now=plane_of(request).clock.now(),
        )
    await _op_audit(request, who, "key.add", body.provider_code, body.reason_code)
    return {"provider_code": body.provider_code, "version": version}
