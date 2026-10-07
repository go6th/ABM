"""Versioned tenant settings (TN5): platform lanes, people scope, exclusions, the pursuing client
and branding references.

* ``TenantSettings`` validates everything a tenant admin may set: lanes and levels must be ids
  from the platform configuration (``config/lanes.yaml``, ``config/people_scope.yaml``), excluded
  companies are normalised domains, excluded people are canonical profile URLs or names, and the
  limits come from ``config/tenant_settings.yaml`` (``status: proposal``).
* Settings are insert-only versions: ``save`` inserts version ``expected + 1``; the primary key
  makes a concurrent save of the same version fail (``SettingsConflict``), never overwrite.
* ``pin_run`` records on the run the version it used (one conditional UPDATE, once).
* ``ExclusionFilter`` is the one implementation of the exclusion rule. It is applied at pull time
  (a run is refused for an excluded company; the people pipeline excludes listed people) and again
  at render time (row sources drop excluded rows), so an excluded entity never reaches a table or
  an export.

GUARANTEE(test_settings_versions_are_insert_only_and_conflict_safe)
GUARANTEE(test_exclusions_hold_at_pull_and_render_time)
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.config import CONFIG_DIR
from people.names import normalise_name

_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_HEX = re.compile(r"^#[0-9a-f]{6}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class SettingsError(ValueError):
    """Settings that fail validation (a 422 at the API)."""


class SettingsInvalid(RuntimeError):
    """The stored latest version no longer validates (for example a lane was removed from the
    platform configuration). Fail closed: no run may start on it and nothing falls back to the
    defaults (that would silently drop exclusions); an admin must save the settings again."""


class SettingsConflict(RuntimeError):
    """Another save created this version first; re-read and retry."""


@dataclass(frozen=True)
class SettingsLimits:
    """Numeric limits from ``config/tenant_settings.yaml``."""

    max_excluded_companies: int
    max_excluded_people: int
    max_pursuing_client_chars: int
    max_regions: int
    max_region_chars: int


@lru_cache
def platform_options() -> tuple[tuple[str, ...], tuple[str, ...], SettingsLimits]:
    """Lane ids, levels in scope and the limits from configuration (read once)."""
    lanes = yaml.safe_load((CONFIG_DIR / "lanes.yaml").read_text())["lanes"]
    scope = yaml.safe_load((CONFIG_DIR / "people_scope.yaml").read_text())
    limits = yaml.safe_load((CONFIG_DIR / "tenant_settings.yaml").read_text())["limits"]
    return (
        tuple(str(lane["id"]) for lane in lanes),
        tuple(str(level) for level in scope["levels_in_scope"]),
        SettingsLimits(**{k: int(v) for k, v in limits.items()}),
    )


def normalise_domain(value: str) -> str:
    """Lower case, no scheme, no ``www.``, no path; SettingsError when not a domain."""
    d = value.strip().lower()
    d = re.sub(r"^https?://", "", d).split("/", 1)[0].removeprefix("www.").rstrip(".")
    if len(d) > 253 or not _DOMAIN.match(d):
        raise SettingsError("not a company domain")
    return d


def normalise_person(value: str) -> str:
    """``url:<host/path>`` for a profile URL (no scheme, ``www.``, query, fragment or trailing
    slash, lower case) or ``name:<normal form>`` for a name (``people.names.normalise_name``, the
    people pipeline's own comparison form)."""
    v = " ".join(value.split())
    if not v or len(v) > 300:
        raise SettingsError("an excluded person must be a non-empty name or profile URL")
    if re.match(r"^https?://", v, re.I):
        v = re.sub(r"^https?://", "", v, flags=re.I).split("?", 1)[0].split("#", 1)[0]
        url = v.lower().removeprefix("www.").rstrip("/")
        if not url or "/" not in url:
            raise SettingsError("an excluded profile URL needs a host and a path")
        return "url:" + url
    name = normalise_name(v)
    if not name:
        raise SettingsError("an excluded name must contain letters or digits")
    return "name:" + name


class PeopleScope(BaseModel):
    """Levels in scope and an optional narrowed geography."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    levels_in_scope: tuple[str, ...]
    regions: tuple[str, ...] = ()


class Exclusions(BaseModel):
    """Companies (domains) and people (profile URLs or names) the tenant excludes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    companies: tuple[str, ...] = ()
    people: tuple[str, ...] = ()

    @field_validator("companies")
    @classmethod
    def _domains(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({normalise_domain(d) for d in v}))

    @field_validator("people")
    @classmethod
    def _people(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({normalise_person(p) for p in v}))


class BrandingRef(BaseModel):
    """Validated branding tokens (``views.branding`` validates the inputs; these are results)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    display_name: str = Field(min_length=1, max_length=60)
    accent_hex: str
    logo_sha256: str | None = None

    @field_validator("accent_hex")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not _HEX.match(v):
            raise ValueError("accent must be #rrggbb")
        return v

    @field_validator("logo_sha256")
    @classmethod
    def _sha(cls, v: str | None) -> str | None:
        if v is not None and not _SHA.match(v):
            raise ValueError("logo reference must be a sha256")
        return v


class TenantSettings(BaseModel):
    """Everything a tenant admin may set, validated against the platform configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    platform_lanes: tuple[str, ...]
    people_scope: PeopleScope
    exclusions: Exclusions = Exclusions()
    pursuing_client: str | None = None
    branding: BrandingRef | None = None

    @model_validator(mode="after")
    def _against_platform(self) -> TenantSettings:
        lanes, levels, limits = platform_options()
        if not self.platform_lanes or len(set(self.platform_lanes)) != len(self.platform_lanes):
            raise ValueError("platform lanes must be a non-empty list without repeats")
        if unknown := set(self.platform_lanes) - set(lanes):
            raise ValueError(f"unknown lanes: {sorted(unknown)}")
        scope = self.people_scope
        if not scope.levels_in_scope or set(scope.levels_in_scope) - set(levels):
            raise ValueError("levels in scope must be a non-empty subset of the platform levels")
        if len(scope.regions) > limits.max_regions or any(
            not r.strip() or len(r) > limits.max_region_chars for r in scope.regions
        ):
            raise ValueError("regions exceed the configured limits")
        if len(self.exclusions.companies) > limits.max_excluded_companies:
            raise ValueError("too many excluded companies")
        if len(self.exclusions.people) > limits.max_excluded_people:
            raise ValueError("too many excluded people")
        if self.pursuing_client is not None:
            name = " ".join(self.pursuing_client.split())
            if not name or len(name) > limits.max_pursuing_client_chars:
                raise ValueError("the pursuing client name is empty or too long")
        return self


def default_settings() -> TenantSettings:
    """Every platform lane and the configured levels; no exclusions, client or branding."""
    lanes, levels, _ = platform_options()
    return TenantSettings(platform_lanes=lanes, people_scope=PeopleScope(levels_in_scope=levels))


def canonical(settings: TenantSettings) -> bytes:
    """Canonical JSON (sorted keys, no whitespace) of the settings."""
    return json.dumps(
        settings.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()


def settings_hash(settings: TenantSettings) -> str:
    """SHA-256 of the canonical JSON."""
    return hashlib.sha256(canonical(settings)).hexdigest()


@dataclass(frozen=True)
class SettingsVersion:
    """One stored version (version 0 is the unsaved platform default)."""

    version: int
    settings: TenantSettings
    hash: str


def parse(data: dict[str, Any]) -> TenantSettings:
    """Validate client input; SettingsError (one type) on any failure."""
    try:
        return TenantSettings.model_validate(data)
    except ValueError as e:
        raise SettingsError(str(e).splitlines()[0]) from None


async def current(session: AsyncSession) -> SettingsVersion:
    """The context tenant's latest settings version, or version 0 (defaults)."""
    row = (
        await session.execute(
            text(
                "SELECT version, settings_json, settings_hash FROM tenant_settings_versions"
                " ORDER BY version DESC LIMIT 1"
            )
        )
    ).first()
    if row is None:
        d = default_settings()
        return SettingsVersion(0, d, settings_hash(d))
    try:
        parsed = TenantSettings.model_validate(row.settings_json)
    except ValueError:
        raise SettingsInvalid(f"settings version {row.version} no longer validates") from None
    return SettingsVersion(int(row.version), parsed, str(row.settings_hash))


async def save(
    session: AsyncSession,
    settings: TenantSettings,
    *,
    actor: str,
    expected_version: int,
    now: datetime,
) -> SettingsVersion:
    """Insert version ``expected_version + 1``; SettingsConflict when it already exists."""
    version = expected_version + 1
    digest = settings_hash(settings)
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO tenant_settings_versions (version, settings_json, settings_hash,"
                    " created_by, created_at) VALUES (:v, CAST(:j AS jsonb), :h, :a, :at)"
                ),
                {
                    "v": version,
                    "j": canonical(settings).decode(),
                    "h": digest,
                    "a": actor,
                    "at": now,
                },
            )
    except IntegrityError:
        raise SettingsConflict(f"settings version {version} already exists") from None
    return SettingsVersion(version, settings, digest)


async def ensure_saved(session: AsyncSession, *, actor: str, now: datetime) -> SettingsVersion:
    """The latest stored version; stores the defaults as version 1 when none exists yet
    (SettingsInvalid when the stored latest no longer validates)."""
    return await _ensure_saved(session, actor=actor, now=now)


async def _ensure_saved(session: AsyncSession, *, actor: str, now: datetime) -> SettingsVersion:
    got = await current(session)
    if got.version > 0:
        return got
    try:
        return await save(session, got.settings, actor=actor, expected_version=0, now=now)
    except SettingsConflict:
        return await current(session)


async def pin_run(session: AsyncSession, run_id: UUID, version: int) -> bool:
    """Record the settings version a run used; True when this call recorded it."""
    result = await session.execute(
        text(
            "UPDATE account_runs SET settings_version = :v"
            " WHERE id = :r AND settings_version IS NULL"
        ),
        {"v": version, "r": run_id},
    )
    return bool(getattr(result, "rowcount", 0))


@dataclass(frozen=True)
class ExclusionFilter:
    """The one exclusion rule: companies by domain (and their subdomains), people by profile URL
    or name, both normalised the way settings store them."""

    companies: frozenset[str]
    people: frozenset[str]

    @classmethod
    def of(cls, settings: TenantSettings) -> ExclusionFilter:
        """The filter for one settings version."""
        return cls(frozenset(settings.exclusions.companies), frozenset(settings.exclusions.people))

    def excludes_company(self, domain: str | None) -> bool:
        """True for an excluded domain or a subdomain of one."""
        if not domain:
            return False
        try:
            d = normalise_domain(domain)
        except SettingsError:
            return False
        return any(d == c or d.endswith("." + c) for c in self.companies)

    def excludes_person(self, name: str | None = None, profile_url: str | None = None) -> bool:
        """True when the person's profile URL or name is excluded."""
        for value in (profile_url, name):
            if value and value.strip():
                try:
                    if normalise_person(value) in self.people:
                        return True
                except SettingsError:
                    continue
        return False

    def run_input(self) -> dict[str, list[str]]:
        """What a run's input carries so the pipelines apply the rule at pull time."""
        return {
            "excluded_people": sorted(self.people),
            "excluded_companies": sorted(self.companies),
        }
