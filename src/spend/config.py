"""Load, validate and hash ``config/spend.yaml`` (startup fails on an invalid file).

Every entry needs a ``source`` or a ``status``; money is parsed with ``spend.money`` (strings,
never floats); a credit rate must carry its own source. ``config_hash`` is recorded on every cap
version.

GUARANTEE(test_spend_config_loads_and_every_entry_is_sourced): no unsourced number is used.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR
from spend.errors import ConfigError
from spend.money import MoneyError, credits, usd_to_micros


@dataclass(frozen=True)
class SpendConfig:
    """The validated spend configuration (money in micro-dollars)."""

    default_cap_micros: int
    default_tool_call_cap: int
    headroom_pct: int
    rounding_step_micros: int
    approver_roles: frozenset[str]
    approval_ttl_minutes: int
    auto_approve: str
    max_extensions: int
    extension_ttl_minutes: int
    max_spend_waves: int
    sample_size: int
    sample_hold_micros: int
    max_snapshot_age_minutes: int
    variance_threshold_pct: int
    allow_fallback_on_exhaustion: bool
    free_operations: frozenset[str]
    categories: dict[str, str]
    credit_usd_rates: dict[str, Decimal] = field(default_factory=dict)
    config_hash: str = ""

    def category(self, operation: str) -> str:
        """The tenant-facing category of an operation (``other`` when unlisted)."""
        return self.categories.get(operation, "other")


def _entry(data: dict[str, Any], key: str) -> Any:
    entry = data.get(key)
    if not isinstance(entry, dict) or "value" not in entry:
        raise ConfigError(f"{key} must be a mapping with a value")
    if "source" not in entry and "status" not in entry:
        raise ConfigError(f"{key} has no source or status")
    return entry["value"]


def _int(data: dict[str, Any], key: str, minimum: int = 0) -> int:
    value = _entry(data, key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{key} must be an integer >= {minimum}")
    return value


def _micros(data: dict[str, Any], key: str) -> int:
    value = _entry(data, key)
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a decimal string (never a float)")
    try:
        return usd_to_micros(value)
    except MoneyError as exc:
        raise ConfigError(f"{key}: {exc}") from exc


def build(data: dict[str, Any], raw: bytes) -> SpendConfig:
    """Validate a parsed mapping (``ConfigError`` on anything wrong)."""
    try:
        if _entry(data, "cap_unit") != "usd":
            raise ConfigError("cap_unit must be usd (D-27)")
        rates: dict[str, Decimal] = {}
        for code, rate in (data.get("credit_usd_rates") or {}).items():
            if (
                not isinstance(rate, dict)
                or "source" not in rate
                or not isinstance(rate.get("value"), str)
            ):
                raise ConfigError(f"credit rate {code} needs a decimal string value and a source")
            rates[str(code)] = credits(rate["value"])
        if "credit_usd_rates_source" not in data and not rates:
            raise ConfigError("credit_usd_rates needs a source")
        cats = data["categories"]
        if "status" not in cats and "source" not in cats:
            raise ConfigError("categories has no source or status")
        if "free_operations_source" not in data:
            raise ConfigError("free_operations needs a source")
        step = _micros(data, "rounding_step_usd")
        if step <= 0:
            raise ConfigError("rounding_step_usd must be positive")
        cap = _micros(data, "default_cap_usd")
        if cap <= 0:
            raise ConfigError("default_cap_usd must be positive")
        auto = _entry(data, "auto_approve")
        if auto not in ("within_run_budget", "never"):
            raise ConfigError("auto_approve must be within_run_budget or never")
        fallback = _entry(data, "allow_fallback_on_exhaustion")
        if not isinstance(fallback, bool):
            raise ConfigError("allow_fallback_on_exhaustion must be a boolean")
        roles = _entry(data, "approver_roles")
        if not isinstance(roles, list) or not roles:
            raise ConfigError("approver_roles must be a non-empty list")
        return SpendConfig(
            default_cap_micros=cap,
            default_tool_call_cap=_int(data, "default_tool_call_cap", 1),
            headroom_pct=_int(data, "headroom_pct"),
            rounding_step_micros=step,
            approver_roles=frozenset(str(r) for r in roles),
            approval_ttl_minutes=_int(data, "approval_ttl_minutes", 1),
            auto_approve=str(auto),
            max_extensions=_int(data, "max_extensions"),
            extension_ttl_minutes=_int(data, "extension_ttl_minutes", 1),
            max_spend_waves=_int(data, "max_spend_waves", 1),
            sample_size=_int(data, "sample_size", 1),
            sample_hold_micros=_micros(data, "sample_hold_usd"),
            max_snapshot_age_minutes=_int(data, "max_snapshot_age_minutes", 1),
            variance_threshold_pct=_int(data, "variance_threshold_pct"),
            allow_fallback_on_exhaustion=fallback,
            free_operations=frozenset(str(o) for o in data.get("free_operations") or []),
            categories={str(k): str(v) for k, v in cats["by_operation"].items()},
            credit_usd_rates=rates,
            config_hash=hashlib.sha256(raw).hexdigest(),
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise ConfigError(f"invalid spend configuration: {exc!r}") from exc


@lru_cache(maxsize=4)
def load_spend_config(directory: Path | None = None) -> SpendConfig:
    """Load and validate ``spend.yaml`` from ``directory`` (default ``CONFIG_DIR``)."""
    path = (directory or CONFIG_DIR) / "spend.yaml"
    try:
        raw = path.read_bytes()
        data = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load {path.name}") from exc
    if not isinstance(data, dict):
        raise ConfigError("spend.yaml must contain a mapping")
    return build(data, raw)


@dataclass(frozen=True)
class V2Config:
    """Credit manager v2 settings (soft and hard limits, pauses, the exhaustion ladder, uploads).
    Every value comes from ``spend.yaml`` ``v2`` with a source or a status."""

    soft_limit_pct: int
    pause_timeout_hours: int
    max_pauses_per_run: int
    tenant_ceiling_micros: int | None
    step_up_max_age_minutes: int
    approver_roles: frozenset[str]
    allow_alternate_on_exhaustion: bool
    exhausted_hold_s: int
    upload_max_bytes: int
    upload_content_types: frozenset[str]
    upload_intent_ttl_minutes: int
    upload_max_age_days: int | None
    notify_max_attempts: int


def build_v2(data: dict[str, Any]) -> V2Config:
    """Validate the ``v2`` section (``ConfigError`` on anything wrong)."""
    v2 = data.get("v2")
    if not isinstance(v2, dict) or ("status" not in v2 and "source" not in v2):
        raise ConfigError("spend.yaml v2 must be a mapping with a source or status")
    try:
        pct = _int(v2, "soft_limit_pct", 1)
        if pct >= 100:
            raise ConfigError("soft_limit_pct must be below 100 (soft < hard, M1)")
        ceiling = _entry(v2, "tenant_ceiling_usd")
        if ceiling is not None and not isinstance(ceiling, str):
            raise ConfigError("tenant_ceiling_usd must be a decimal string or null")
        alternate = _entry(v2, "allow_alternate_on_exhaustion")
        if not isinstance(alternate, bool):
            raise ConfigError("allow_alternate_on_exhaustion must be a boolean")
        roles = _entry(v2, "approver_roles")
        types = _entry(v2, "upload_content_types")
        if not isinstance(roles, list) or not roles or not isinstance(types, list) or not types:
            raise ConfigError("approver_roles and upload_content_types must be non-empty lists")
        age = _entry(v2, "upload_max_age_days")
        if age is not None and (isinstance(age, bool) or not isinstance(age, int) or age < 1):
            raise ConfigError("upload_max_age_days must be a positive integer or null")
        return V2Config(
            soft_limit_pct=pct,
            pause_timeout_hours=_int(v2, "pause_timeout_hours", 1),
            max_pauses_per_run=_int(v2, "max_pauses_per_run", 1),
            tenant_ceiling_micros=usd_to_micros(ceiling) if ceiling is not None else None,
            step_up_max_age_minutes=_int(v2, "step_up_max_age_minutes", 1),
            approver_roles=frozenset(str(r) for r in roles),
            allow_alternate_on_exhaustion=alternate,
            exhausted_hold_s=_int(v2, "exhausted_hold_s", 1),
            upload_max_bytes=_int(v2, "upload_max_bytes", 1),
            upload_content_types=frozenset(str(t) for t in types),
            upload_intent_ttl_minutes=_int(v2, "upload_intent_ttl_minutes", 1),
            upload_max_age_days=age,
            notify_max_attempts=_int(v2, "notify_max_attempts", 1),
        )
    except MoneyError as exc:
        raise ConfigError(f"v2: {exc}") from exc


@lru_cache(maxsize=4)
def load_v2_config(directory: Path | None = None) -> V2Config:
    """Load and validate the ``v2`` section of ``spend.yaml``."""
    path = (directory or CONFIG_DIR) / "spend.yaml"
    try:
        data = yaml.safe_load(path.read_bytes())
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load {path.name}") from exc
    if not isinstance(data, dict):
        raise ConfigError("spend.yaml must contain a mapping")
    return build_v2(data)
