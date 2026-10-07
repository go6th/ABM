"""Saved views (``/v1/views``; VW3, D-61). A view stores a validated query spec, never SQL.

* ``GET /v1/views``: the caller's own views and the tenant's shared views.
* ``POST /v1/views``: save one (sharing needs ``edit_shared_views``: admin and analyst).
* ``PUT /v1/views/{view_id}``: edit at the version read (owner, or a shared-view editor).
* ``POST /v1/views/{view_id}/archive``: archive (never delete).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from views import saved
from views.query import QuerySpec
from views.tables import specs

router = APIRouter(prefix="/views", tags=["views"])
register("GET", "/v1/views", tenant_route("read_public", "read"))
register("POST", "/v1/views", tenant_route("save_own_view", "admin"))
register("PUT", "/v1/views/{view_id}", tenant_route("save_own_view", "admin"))
register("POST", "/v1/views/{view_id}/archive", tenant_route("save_own_view", "admin"))
Table = Literal["people", "jobs", "tech", "facts", "coverage"]


class ViewBody(BaseModel):
    """A view to save. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    table: Table
    name: str = Field(min_length=1, max_length=80)
    spec: QuerySpec
    shared: bool = False


class ViewEdit(BaseModel):
    """An edit at the version read."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    spec: QuerySpec | None = None
    shared: bool | None = None


class Archive(BaseModel):
    """Archive at the version read."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


def _problem(e: saved.SavedViewError) -> APIProblem:
    status = {
        "not_found": 404,
        "not_allowed": 403,
        "version_conflict": 409,
        "limit_reached": 409,
    }.get(e.code, 422)
    return APIProblem(status, e.code, "Saved view refused", "The saved view change was refused.")


@router.get("")
async def list_views(
    request: Request,
    table: Table | None = Query(default=None),
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """The caller's and the tenant's shared views."""
    async with tenant_txn(ident.tenant_id) as s:
        views = await saved.listing(s, user=ident.user_id, table=table)
    return {"views": [saved.as_json(v) for v in views]}


@router.post("", status_code=201)
async def create_view(
    body: ViewBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Save a view."""
    try:
        async with tenant_txn(ident.tenant_id) as s:
            view = await saved.create(
                s,
                owner=ident.user_id,
                role=ident.role,
                table=specs()[body.table],
                name=body.name,
                spec=body.spec,
                shared=body.shared,
                now=plane_of(request).clock.now(),
            )
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="view_save",
                resource_type="saved_view",
                resource_id=view.id,
            )
    except saved.SavedViewError as e:
        raise _problem(e) from None
    return saved.as_json(view)


@router.put("/{view_id}")
async def edit_view(
    view_id: UUID, body: ViewEdit, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Edit a view."""
    try:
        async with tenant_txn(ident.tenant_id) as s:
            current = await saved.get(s, view_id, user=ident.user_id)
            view = await saved.update(
                s,
                view_id,
                user=ident.user_id,
                role=ident.role,
                table=specs()[current.table],
                expected_version=body.expected_version,
                now=plane_of(request).clock.now(),
                name=body.name,
                spec=body.spec,
                shared=body.shared,
            )
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="view_edit",
                resource_type="saved_view",
                resource_id=view_id,
            )
    except saved.SavedViewError as e:
        raise _problem(e) from None
    return saved.as_json(view)


@router.post("/{view_id}/archive")
async def archive_view(
    view_id: UUID, body: Archive, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Archive a view (it is never deleted)."""
    try:
        async with tenant_txn(ident.tenant_id) as s:
            current = await saved.get(s, view_id, user=ident.user_id)
            view = await saved.update(
                s,
                view_id,
                user=ident.user_id,
                role=ident.role,
                table=specs()[current.table],
                expected_version=body.expected_version,
                now=plane_of(request).clock.now(),
                archive=True,
            )
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="view_archive",
                resource_type="saved_view",
                resource_id=view_id,
            )
    except saved.SavedViewError as e:
        raise _problem(e) from None
    return {"id": str(view.id), "archived": True}
