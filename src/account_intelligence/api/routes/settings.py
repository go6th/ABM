"""Tenant settings (``/v1/settings``; TN5).

* ``GET /v1/settings``: the latest version (every role: sellers need the lanes to start a run).
* ``PUT /v1/settings``: an admin saves a new version at ``expected_version + 1`` (insert-only;
  a concurrent save of the same version is a 409, never an overwrite).
* Branding (VW6): ``POST /v1/settings/branding/logo`` (raw PNG or JPEG body; quarantined,
  scanned, re-encoded), ``PUT /v1/settings/branding`` (validated tokens, a new settings
  version), ``GET /v1/branding`` (the tokens; the attribution is fixed).
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.ports import NotConfigured
from tenancy import settings
from tenancy.storage import tenant_path
from views import branding

router = APIRouter(prefix="/settings", tags=["settings"])
branding_router = APIRouter(tags=["settings"])
register("GET", "/v1/settings", tenant_route("read_public", "read"))
register("PUT", "/v1/settings", tenant_route("manage_settings", "admin"))
# People data consent (G8, D-97): any member reads it; an admin grants or withdraws it. A
# research run pulls people only while the newest record grants the current text version.
register("GET", "/v1/settings/people-consent", tenant_route("read_public", "read"))
register("POST", "/v1/settings/people-consent", tenant_route("manage_settings", "admin"))


class PeopleConsentBody(BaseModel):
    """Grant (``accept`` true) or withdraw (false) the people data consent. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    accept: bool


def _consent_text() -> tuple[str, str, str, str]:
    """``(scope, label, text, version)`` from the skill config (one source, one reader)."""
    from agents.config import skill_config

    cfg = skill_config()
    return cfg.consent_scope, cfg.consent_label, cfg.consent_text, cfg.consent_text_version or ""


async def _consent_json(tenant_id: Any) -> dict[str, Any]:
    from agents.adapters import PgConsentStore

    scope, label, body, version = _consent_text()
    record = await PgConsentStore().current(tenant_id, scope)
    granted = record is not None and record.revoked_at is None
    return {
        "scope": scope,
        "label": label,
        "text": body,
        "text_version": version,
        "granted": granted,
        "current": bool(granted and record is not None and record.text_version == version),
        "granted_by": record.granted_by if record is not None else None,
        "granted_at": record.granted_at if record is not None else None,
        "revoked_at": record.revoked_at if record is not None else None,
    }


@router.get("/people-consent", response_model=C.PeopleConsent)
async def read_people_consent(
    request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """The consent text, its version, and whether this workspace has granted it."""
    return await _consent_json(ident.tenant_id)


@router.post("/people-consent", response_model=C.PeopleConsent)
async def set_people_consent(
    body: PeopleConsentBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Grant or withdraw the consent (insert-only rows; a withdrawal is a newer row)."""
    from agents.adapters import PgConsentStore

    scope, _, _, version = _consent_text()
    if not version:
        raise APIProblem(
            409,
            "consent_text_not_approved",
            "No consent text to accept",
            "The people data consent text has no approved version yet.",
        )
    now = plane_of(request).clock.now()
    await PgConsentStore().record(
        ident.tenant_id,
        ident.user_id,
        version,
        frozenset({scope}),
        now,
        revoked=not body.accept,
    )
    async with tenant_txn(ident.tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="people_consent_granted" if body.accept else "people_consent_withdrawn",
            resource_type="people_consent",
            resource_id=version,
        )
    return await _consent_json(ident.tenant_id)


class SettingsBody(BaseModel):
    """The new settings and the version they were read at. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    settings: dict[str, Any]


def _json(v: settings.SettingsVersion) -> dict[str, Any]:
    return {"version": v.version, "hash": v.hash, "settings": v.settings.model_dump(mode="json")}


@router.get("", response_model=C.TenantSettings)
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


@router.put("", response_model=C.TenantSettings)
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
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="settings_save",
                resource_type="settings",
                resource_id=saved.version,
            )
    except settings.SettingsConflict:
        raise HTTPException(409, "settings changed since they were read") from None
    return _json(saved)


register("GET", "/v1/branding", tenant_route("read_public", "read"))
register("POST", "/v1/settings/branding/logo", tenant_route("manage_settings", "admin"))
register("PUT", "/v1/settings/branding", tenant_route("manage_settings", "admin"))


class BrandingBody(BaseModel):
    """Branding tokens at the settings version read. No attribution field exists (D-64)."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    display_name: str = Field(min_length=1, max_length=200)
    accent_hex: str = Field(min_length=4, max_length=20)
    logo_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@branding_router.get("/branding", response_model=C.Branding)
async def read_branding(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The tokens views and documents use (the attribution is fixed)."""
    try:
        async with tenant_txn(ident.tenant_id) as s:
            current = await settings.current(s)
    except settings.SettingsInvalid:
        raise APIProblem(
            409,
            "settings_invalid",
            "Settings need attention",
            "An admin must save the tenant settings again.",
        ) from None
    return branding.tokens(current.settings.branding)


@router.post("/branding/logo", response_model=C.LogoUploaded, status_code=201)
async def upload_logo(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """Upload a logo: quarantined and scanned BEFORE any decoder sees it, then re-encoded."""
    limits = branding.load_limits()
    body = await request.body()
    if not body or len(body) > limits.max_logo_bytes:
        raise APIProblem(413, "logo_too_large", "Logo too large", "The logo is too large.")
    try:
        branding.sniff(body)
    except branding.BrandingRefused as e:
        raise APIProblem(
            422, e.code, "Logo refused", "Only PNG or JPEG logos are accepted."
        ) from None
    plane = plane_of(request)
    original = hashlib.sha256(body).hexdigest()
    quarantine = tenant_path(ident.tenant_id, "uploads", "branding", original)
    await plane.objects.put_if_absent(quarantine, body, "application/octet-stream")
    try:
        clean = await plane.scanner.scan(quarantine)
    except NotConfigured:
        raise APIProblem(
            503,
            "scanner_unavailable",
            "Scanner unavailable",
            "The upload could not be scanned and was not accepted.",
        ) from None
    if not clean:
        raise APIProblem(422, "logo_infected", "Logo refused", "The upload failed the scan.")
    try:
        logo = branding.process_logo(body, limits)
    except branding.BrandingRefused as e:
        raise APIProblem(422, e.code, "Logo refused", "The logo could not be accepted.") from None
    key = tenant_path(ident.tenant_id, "branding", f"{logo.sha256}.png")
    await plane.objects.put_if_absent(key, logo.png, "image/png")
    async with tenant_txn(ident.tenant_id) as s:
        await branding.record_asset(s, logo, key, ident.user_id, plane.clock.now())
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="logo_upload",
            resource_type="branding_asset",
            resource_id=logo.sha256,
        )
    return {"logo_sha256": logo.sha256, "width": logo.width, "height": logo.height}


@router.put("/branding", response_model=C.BrandingSaved)
async def save_branding(
    body: BrandingBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Save branding as a new settings version (validated tokens only)."""
    try:
        name = branding.display_name(body.display_name)
        colour = branding.accent(body.accent_hex)
    except branding.BrandingRefused as e:
        raise APIProblem(
            422, e.code, "Branding refused", "The branding was not accepted."
        ) from None
    try:
        async with tenant_txn(ident.tenant_id) as s:
            logo = body.logo_sha256
            if logo is not None and not await branding.asset_exists(s, logo):
                raise APIProblem(
                    422, "unknown_logo", "Unknown logo", "Upload the logo before using it."
                )
            current = await settings.current(s)
            new = current.settings.model_copy(
                update={
                    "branding": settings.BrandingRef(
                        display_name=name, accent_hex=colour, logo_sha256=body.logo_sha256
                    )
                }
            )
            saved = await settings.save(
                s,
                new,
                actor=ident.user_id,
                expected_version=body.expected_version,
                now=plane_of(request).clock.now(),
            )
            await audit_action(
                s,
                request,
                audit_class="admin",
                action="branding_save",
                resource_type="settings",
                resource_id=saved.version,
            )
    except settings.SettingsConflict:
        raise HTTPException(409, "settings changed since they were read") from None
    except settings.SettingsInvalid:
        raise APIProblem(
            409,
            "settings_invalid",
            "Settings need attention",
            "An admin must save the tenant settings again.",
        ) from None
    return {"version": saved.version, **branding.tokens(new.branding)}
