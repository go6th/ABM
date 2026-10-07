"""Tenant settings (``/v1/settings``; TN5).

* ``GET /v1/settings``: the latest version (every role: sellers need the lanes to start a run).
* ``PUT /v1/settings``: an admin saves a new version at ``expected_version + 1`` (insert-only;
  a concurrent save of the same version is a 409, never an overwrite).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from tenancy import settings

router = APIRouter(prefix="/settings", tags=["settings"])
register("GET", "/v1/settings", tenant_route("read_public", "read"))
register("PUT", "/v1/settings", tenant_route("manage_settings", "admin"))


class SettingsBody(BaseModel):
    """The new settings and the version they were read at. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    settings: dict[str, Any]


def _json(v: settings.SettingsVersion) -> dict[str, Any]:
    return {"version": v.version, "hash": v.hash, "settings": v.settings.model_dump(mode="json")}


@router.get("")
async def read_settings(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The tenant's current settings (version 0 is the platform default, never stored)."""
    try:
        async with tenant_txn(ident.tenant_id) as s:
            return _json(await settings.current(s))
    except settings.SettingsInvalid:
        raise APIProblem(
            409,
            "settings_invalid",
            "Settings need attention",
            "The saved settings no longer match the platform options; an admin must save them"
            " again.",
        ) from None


@router.put("")
async def save_settings(
    body: SettingsBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Save a new version."""
    try:
        parsed = settings.parse(body.settings)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e)) from None
    try:
        async with tenant_txn(ident.tenant_id) as s:
            saved = await settings.save(
                s,
                parsed,
                actor=ident.user_id,
                expected_version=body.expected_version,
                now=plane_of(request).clock.now(),
            )
    except settings.SettingsConflict:
        raise HTTPException(409, "settings changed since they were read") from None
    return _json(saved)
