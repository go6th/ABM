"""The 6th Library over the API (``replica.library``).

Client (session tenant):

* ``GET /v1/library/available``: the library accounts published to this client, each with
  whether it is already in the workspace, and the caller's remaining account places.
* ``POST /v1/accounts/from-library`` {library_account_ids}: add them to the workspace. Every
  new account counts against the caller's account cap (checked for the whole selection first:
  409 ``account_cap_reached``); no credits are used; an account already in the workspace is left
  as it is. An id not published to this client is 404.

Operator (6th staff, no tenant content):

* ``GET /operator/v1/library``: every current library account with its check and how many
  clients it is published to.
* ``POST /operator/v1/library/uploads?filename=&domain=&account_name=``: the package as the raw
  request body (a workspace zip or one standing file, at most 100 MB), inspected and stored; the
  internal-only file is left out and never stored.
* ``POST /operator/v1/library/{library_account_id}/publish`` {tenant_ids} and ``/unpublish``:
  publish a passed account to chosen clients (each in that client's own transaction), or stop
  offering it. Publishing an account that failed its check is 409 ``check_failed``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.operator import StaffIdentity, require_staff
from account_intelligence.api.policy import OperatorRole, operator_route, register, tenant_route
from account_intelligence.db.session import tenant_txn, untenanted_session
from account_intelligence.replica import library as L
from account_intelligence.replica.settings import (
    accounts_used,
    client_settings,
)

router = APIRouter(tags=["library"])
operator_router = APIRouter(prefix="/operator/v1/library", tags=["operator"])
OPS: frozenset[OperatorRole] = frozenset({"operator"})
register("GET", "/v1/library/available", tenant_route("read_public", "read", step_up=False))
register("POST", "/v1/accounts/from-library", tenant_route("start_run", "run", step_up=False))
register("GET", "/operator/v1/library", operator_route("read", tenant="none", roles=OPS))
register("POST", "/operator/v1/library/uploads", operator_route("admin", tenant="none", roles=OPS))
for _p in ("publish", "unpublish"):
    register(
        "POST",
        f"/operator/v1/library/{{library_account_id}}/{_p}",
        operator_route("admin", tenant="none", roles=OPS),
    )

MAX_UPLOAD = 100 * 1024 * 1024
DOMAIN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$"


@router.get("/library/available", response_model=C.LibraryAvailable)
async def library_available(ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The ready-made research this client may add, and the caller's remaining places."""
    async with tenant_txn(ident.tenant_id) as s:
        items = await L.available(s)
        cap = (await client_settings(s)).account_cap_per_user
        used = await accounts_used(s, ident.user_id)
    return {
        "accounts": items,
        "accounts_used": used,
        "account_cap": cap,
        "left": max(0, cap - used),
    }


class FromLibrary(BaseModel):
    """Library accounts to add (ids only; the tenant comes from the session)."""

    model_config = ConfigDict(extra="forbid")
    library_account_ids: list[UUID] = Field(min_length=1, max_length=50)


@router.post("/accounts/from-library", response_model=C.LibraryAdded, status_code=201)
async def from_library(
    body: FromLibrary, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Copy the chosen library accounts into the workspace (no credits; counts toward the cap)."""
    now = datetime.now(UTC)
    payloads = request.app.state.container.payloads
    wanted = list(dict.fromkeys(body.library_account_ids))
    async with tenant_txn(ident.tenant_id) as s:
        offered = {a["id"]: a for a in await L.available(s)}
        if any(str(i) not in offered for i in wanted):
            raise HTTPException(404, "not found")
        new = [i for i in wanted if not offered[str(i)]["in_workspace"]]
        added = []
        for library_id in new:
            done = await L.add_to_workspace(
                s, payloads, ident.tenant_id, library_id, ident.user_id, now
            )
            if done.created:
                added.append(done.domain)
        await audit_action(
            s,
            request,
            audit_class="run",
            action="add_from_library",
            resource_type="account",
            resource_id=None,
        )
    return {"added": added, "already_in_workspace": len(wanted) - len(new)}


@operator_router.get("", response_model=C.LibraryList)
async def library_list(who: StaffIdentity = Depends(require_staff)) -> dict[str, Any]:
    """Every current library account (platform rows; counts only from the clients)."""
    async with untenanted_session() as plain:
        return {"accounts": await L.library_list(plain)}


@operator_router.post("/uploads", response_model=C.LibraryUploaded, status_code=201)
async def library_upload(
    request: Request,
    filename: str = Query(min_length=1, max_length=255, pattern=r"^[^/\\]+$"),
    domain: str = Query(pattern=DOMAIN, max_length=253),
    account_name: str | None = Query(default=None, max_length=200),
    who: StaffIdentity = Depends(require_staff),
) -> dict[str, Any]:
    """Inspect and store one library package (the request body is the file)."""
    data = await request.body()
    if not data or len(data) > MAX_UPLOAD:
        raise APIProblem(413, "package_size", "Package size", "Upload a file up to 100 MB.")
    try:
        found = L.inspect_package(filename, data, account_name, domain)
    except L.LibraryError as error:
        raise APIProblem(422, "package_unreadable", "Package not readable", str(error)) from None
    async with untenanted_session() as plain:
        account_id = await L.store_upload(
            plain, request.app.state.container.payloads, filename, data, found, who.staff_id
        )
    await _op_audit(request, who, "library.upload", str(account_id))
    return {
        "id": str(account_id),
        "name": found.name,
        "deliverables_found": len(found.files),
        "internal_left_out": found.internal_left_out,
        "check_passed": found.check_passed,
        "issues": found.issues,
    }


class PublishBody(BaseModel):
    """The clients to publish to (or stop publishing to)."""

    model_config = ConfigDict(extra="forbid")
    tenant_ids: list[UUID] = Field(min_length=1, max_length=500)


@operator_router.post("/{library_account_id}/publish", response_model=C.LibraryPublished)
async def library_publish(
    library_account_id: UUID,
    body: PublishBody,
    request: Request,
    who: StaffIdentity = Depends(require_staff),
) -> dict[str, Any]:
    """Publish a passed library account to each chosen client."""
    async with untenanted_session() as plain:
        if not await L.check_passed(plain, library_account_id):
            raise APIProblem(
                409,
                "check_failed",
                "Not ready for clients",
                "Only an account whose client-ready check passed can be published.",
            )
    published = 0
    for tenant in dict.fromkeys(body.tenant_ids):
        async with tenant_txn(tenant) as s:
            published += await L.publish(s, library_account_id, who.staff_id)
    await _op_audit(request, who, "library.publish", str(library_account_id))
    return {"published": published}


@operator_router.post("/{library_account_id}/unpublish", response_model=C.LibraryPublished)
async def library_unpublish(
    library_account_id: UUID,
    body: PublishBody,
    request: Request,
    who: StaffIdentity = Depends(require_staff),
) -> dict[str, Any]:
    """Stop offering a library account to each chosen client (accounts already added stay)."""
    for tenant in dict.fromkeys(body.tenant_ids):
        async with tenant_txn(tenant) as s:
            await L.unpublish(s, library_account_id)
    await _op_audit(request, who, "library.unpublish", str(library_account_id))
    return {"published": 0}


async def _op_audit(request: Request, who: StaffIdentity, action: str, target: str) -> None:
    from account_intelligence.api.tenant_plane import plane_of
    from admin import tenants

    async with untenanted_session() as plain:
        await tenants.audit(
            plain,
            operator=who.staff_id,
            action=action,
            target=target,
            reason_code=None,
            request_id=getattr(request.state, "request_id", None),
            now=plane_of(request).clock.now(),
        )
