"""Load, validate and hash ``config/parallel.yaml`` (startup fails on an invalid file).

Every number and list carries a ``source`` or a ``status`` (no invented rates, limits or
durations: section 2.1); a provider ceiling below the floor, a share outside 1 to 100 or an unknown
fan-out kind is ``ConfigInvalid``. The DAG itself is validated by ``parallel.dag``.

GUARANTEE(test_parallel_config_loads_and_every_entry_is_sourced): no unsourced value is used.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from parallel.errors import ConfigInvalid

FAN_OUT_KINDS = ("once", "lane", "lane_pass", "rows:")
GRANULARITIES = ("stage", "unit")


@dataclass(frozen=True)
class UnitTypeSpec:
    """One configured unit type."""

    name: str
    group: str
    fan_out: str
    depends_on: tuple[str, ...]
    soft: frozenset[str]
    provider_route: str | None
    basis: str


@dataclass(frozen=True)
class BodySpec:
    """The code body that executes a set of unit types."""

    name: str
    granularity: str
    covers: tuple[str, ...]
    allocates_sources: bool = False


@dataclass(frozen=True)
class ProviderSettings:
    """Governor knobs (all proposals until measured, D-43)."""

    floor: int
    default_ceiling: int
    ceilings: Mapping[str, int]
    aimd_window_s: float
    lease_ttl_s: float
    heartbeat_s: float
    lease_wait_s: float
    lease_poll_s: float
    lease_jitter_s: float
    waiter_ttl_s: float

    def ceiling(self, provider_code: str) -> int:
        """The configured ceiling for a provider (the default when none is configured)."""
        return int(self.ceilings.get(provider_code, self.default_ceiling))


@dataclass(frozen=True)
class ParallelConfig:
    """The validated configuration and its hash."""

    version: str
    lanes: tuple[str, ...]
    seniority_passes: tuple[int, ...]
    name_search_categories: tuple[str, ...]
    unit_types: Mapping[str, UnitTypeSpec]
    sequential_tail: tuple[str, ...]
    dynamic_caps: Mapping[str, int]
    bodies: Mapping[str, BodySpec]
    providers: ProviderSettings
    run_share_pct: int
    tenant_share_pct: int
    aging_step_s: float
    max_in_flight_units: int
    max_queue: int
    deadline_ceiling_s: float
    tail_reserve_s: float
    unmeasured_p95_s: float
    min_samples_for_p95: int
    unit_attempts: int
    config_hash: str


def _sourced(data: Mapping[str, Any], key: str) -> Any:
    entry = data.get(key)
    if not isinstance(entry, Mapping) or "value" not in entry:
        raise ConfigInvalid(f"{key} must be a mapping with a value")
    if "source" not in entry and "status" not in entry:
        raise ConfigInvalid(f"{key} has no source or status")
    return entry["value"]


def _section(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    entry = data.get(key)
    if not isinstance(entry, Mapping):
        raise ConfigInvalid(f"{key} must be a mapping")
    if "source" not in entry and "status" not in entry:
        raise ConfigInvalid(f"{key} has no source or status")
    return entry


def _pos_int(value: Any, key: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigInvalid(f"{key} must be an integer >= {minimum}")
    return value


def _pos_num(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigInvalid(f"{key} must be a positive number")
    return float(value)


def _unit_types(raw: Mapping[str, Any]) -> dict[str, UnitTypeSpec]:
    if not isinstance(raw, Mapping) or not raw:
        raise ConfigInvalid("unit_types must be a non-empty mapping")
    out: dict[str, UnitTypeSpec] = {}
    for name, item in raw.items():
        if not isinstance(item, Mapping) or not item.get("basis"):
            raise ConfigInvalid(f"unit type {name} needs a basis (its source statement)")
        fan_out = str(item.get("fan_out", ""))
        if not (fan_out in FAN_OUT_KINDS[:3] or fan_out.startswith("rows:")):
            raise ConfigInvalid(f"unit type {name} has an unknown fan_out {fan_out!r}")
        deps = tuple(str(d) for d in item.get("depends_on") or [])
        soft = frozenset(str(d) for d in item.get("soft") or [])
        if not soft <= set(deps):
            raise ConfigInvalid(f"unit type {name}: a soft dependency must also be a dependency")
        route = item.get("provider_route")
        out[str(name)] = UnitTypeSpec(
            name=str(name),
            group=str(item.get("group", "")),
            fan_out=fan_out,
            depends_on=deps,
            soft=soft,
            provider_route=None if route in (None, "none") else str(route),
            basis=str(item["basis"]),
        )
    return out


def _bodies(raw: Mapping[str, Any]) -> dict[str, BodySpec]:
    value = _sourced(raw, "bodies")
    if not isinstance(value, Mapping) or not value:
        raise ConfigInvalid("bodies must be a non-empty mapping")
    out: dict[str, BodySpec] = {}
    for name, item in value.items():
        granularity = str(item.get("granularity"))
        if granularity not in GRANULARITIES:
            raise ConfigInvalid(f"body {name} has an unknown granularity {granularity!r}")
        covers = tuple(str(c) for c in item.get("covers") or [])
        if not covers:
            raise ConfigInvalid(f"body {name} covers no unit type")
        out[str(name)] = BodySpec(
            str(name), granularity, covers, item.get("allocates_sources") is True
        )
    return out


def build(data: Mapping[str, Any], raw: bytes) -> ParallelConfig:
    """Validate a parsed mapping (``ConfigInvalid`` on anything wrong)."""
    try:
        providers = _section(data, "providers")
        fair = _section(data, "fairness")
        worker = _section(data, "worker")
        deadline = data["deadline"]
        floor = _pos_int(providers["floor"], "providers.floor")
        default_ceiling = _pos_int(
            _sourced(providers, "default_ceiling"), "providers.default_ceiling"
        )
        ceilings = {str(k): _pos_int(v, f"ceiling {k}") for k, v in providers["ceilings"].items()}
        if default_ceiling < floor or any(c < floor for c in ceilings.values()):
            raise ConfigInvalid("a provider ceiling cannot be below the floor")
        settings = ProviderSettings(
            floor=floor,
            default_ceiling=default_ceiling,
            ceilings=ceilings,
            aimd_window_s=_pos_num(_sourced(providers, "aimd_window_s"), "aimd_window_s"),
            lease_ttl_s=_pos_num(_sourced(providers, "lease_ttl_s"), "lease_ttl_s"),
            heartbeat_s=_pos_num(_sourced(providers, "heartbeat_s"), "heartbeat_s"),
            lease_wait_s=_pos_num(_sourced(providers, "lease_wait_s"), "lease_wait_s"),
            lease_poll_s=_pos_num(_sourced(providers, "lease_poll_s"), "lease_poll_s"),
            lease_jitter_s=_pos_num(_sourced(providers, "lease_jitter_s"), "lease_jitter_s"),
            waiter_ttl_s=_pos_num(_sourced(providers, "waiter_ttl_s"), "waiter_ttl_s"),
        )
        if settings.heartbeat_s >= settings.lease_ttl_s:
            raise ConfigInvalid("the heartbeat must be shorter than the lease ttl")
        run_share = _pos_int(fair["run_share_pct"], "run_share_pct")
        tenant_share = _pos_int(fair["tenant_share_pct"], "tenant_share_pct")
        if run_share > 100 or tenant_share > 100:
            raise ConfigInvalid("a share is a percentage between 1 and 100")
        caps = {
            str(k): _pos_int(_sourced(data["dynamic_caps"], k), f"dynamic_caps.{k}")
            for k in data["dynamic_caps"]
        }
        for needed in ("top_people", "senior_people", "priority_a_people", "units_per_run"):
            if needed not in caps:
                raise ConfigInvalid(f"dynamic_caps.{needed} is required")
        return ParallelConfig(
            version=str(data["version"]),
            lanes=tuple(str(x) for x in _sourced(data, "lanes")),
            seniority_passes=tuple(int(x) for x in _sourced(data, "seniority_passes")),
            name_search_categories=tuple(str(x) for x in _sourced(data, "name_search_categories")),
            unit_types=_unit_types(data["unit_types"]),
            sequential_tail=tuple(str(x) for x in _sourced(data, "sequential_tail")),
            dynamic_caps=caps,
            bodies=_bodies(data),
            providers=settings,
            run_share_pct=run_share,
            tenant_share_pct=tenant_share,
            aging_step_s=_pos_num(fair["aging_step_s"], "aging_step_s"),
            max_in_flight_units=_pos_int(worker["max_in_flight_units"], "max_in_flight_units"),
            max_queue=_pos_int(worker["max_queue"], "max_queue"),
            deadline_ceiling_s=_pos_num(_sourced(deadline, "ceiling_s"), "ceiling_s"),
            tail_reserve_s=_pos_num(_sourced(deadline, "tail_reserve_s"), "tail_reserve_s"),
            unmeasured_p95_s=_pos_num(_sourced(deadline, "unmeasured_p95_s"), "unmeasured_p95_s"),
            min_samples_for_p95=_pos_int(
                _sourced(deadline, "min_samples_for_p95"), "min_samples_for_p95"
            ),
            unit_attempts=_pos_int(_sourced(data, "unit_attempts"), "unit_attempts"),
            config_hash=hashlib.sha256(raw).hexdigest(),
        )
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, ConfigInvalid):
            raise
        raise ConfigInvalid(f"invalid parallel configuration: {exc!r}") from exc


def config_dir() -> Path:
    """The repository's ``config`` directory."""
    return Path(__file__).resolve().parents[2] / "config"


@lru_cache(maxsize=4)
def load_parallel_config(directory: Path | None = None) -> ParallelConfig:
    """Load, validate and hash ``parallel.yaml``; the DAG is validated too (startup gate)."""
    path = (directory or config_dir()) / "parallel.yaml"
    try:
        raw = path.read_bytes()
        data = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigInvalid(f"cannot load {path.name}") from exc
    if not isinstance(data, Mapping):
        raise ConfigInvalid("parallel.yaml must contain a mapping")
    cfg = build(data, raw)
    from parallel.dag import validate  # local import: dag depends on this module's types

    validate(cfg)
    return cfg
