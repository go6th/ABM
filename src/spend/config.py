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
