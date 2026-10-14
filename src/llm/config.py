"""Load, validate and hash ``config/model_routing.yaml`` (R1, R7; startup fails on anything wrong).

Every task kind names a known tier; every tier has at least one candidate; every candidate declares
its adapter, provider code, family, model, context window, data classes, retention, caching and
price source. A fact the sources do not give is ``null`` and surfaced by ``warnings()``, never
defaulted. ``config_hash`` and ``routing_version`` are pinned on every call record.

GUARANTEE(test_routing_config_loads_and_every_candidate_fact_is_sourced): R1.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from llm.errors import ConfigInvalid

DATA_CLASSES = frozenset({"public_web", "tenant", "private"})
ADAPTERS = frozenset({"anthropic_api", "vertex_ai", "fake"})


@dataclass(frozen=True)
class TaskKind:
    """One task kind and its fixed knobs (R7)."""

    name: str
    tier: str
    max_tokens: int
    temperature: float | None
    cacheable: bool


@dataclass(frozen=True)
class Tier:
    """An ordered candidate ladder."""

    name: str
    ladder: tuple[str, ...]
    fallback_after_retryable: int


@dataclass(frozen=True)
class Price:
    """USD per million tokens; None when no source gives it (never zero)."""

    input_per_mtok: Decimal | None
    output_per_mtok: Decimal | None
    source: str


@dataclass(frozen=True)
class Candidate:
    """One model candidate as configured."""

    name: str
    adapter: str
    provider_code: str
    family: str
    model: str
    context_window: int | None
    data_classes: frozenset[str]
    zero_retention: bool | None
    caching: bool | None
    price: Price


@dataclass(frozen=True)
class RoutingConfig:
    """The validated routing configuration."""

    version: str
    routing_version: str
    task_kinds: Mapping[str, TaskKind]
    tiers: Mapping[str, Tier]
    judge_tier: str
    candidates: Mapping[str, Candidate]
    token_caps: Mapping[str, int | None]
    health_threshold: int
    health_window_s: float
    health_cooldown_s: float
    cache_floor: float
    schema_reasks: int
    benchmark_report: str | None
    config_hash: str

    def warnings(self) -> tuple[str, ...]:
        """Unknown candidate facts (surfaced at startup, never defaulted)."""
        out: list[str] = []
        for c in self.candidates.values():
            for label, value in (
                ("context_window", c.context_window),
                ("zero_retention", c.zero_retention),
                ("caching", c.caching),
                ("input price", c.price.input_per_mtok),
                ("output price", c.price.output_per_mtok),
            ):
                if value is None:
                    out.append(f"{c.name}: {label} unknown")
        return tuple(out)


def _fact(entry: Any, key: str) -> Any:
    if not isinstance(entry, Mapping) or "value" not in entry:
        raise ConfigInvalid(f"{key} must be a mapping with a value")
    if "source" not in entry and "status" not in entry:
        raise ConfigInvalid(f"{key} has no source or status")
    return entry["value"]


def _section(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    entry = data.get(key)
    if not isinstance(entry, Mapping) or ("source" not in entry and "status" not in entry):
        raise ConfigInvalid(f"{key} must be a mapping with a source or status")
    return entry


def _price(raw: Any, name: str) -> Price:
    if not isinstance(raw, Mapping) or "source" not in raw:
        raise ConfigInvalid(f"candidate {name} price needs a source")

    def money(v: Any) -> Decimal | None:
        if v is None:
            return None
        try:
            d = Decimal(str(v))
        except InvalidOperation as exc:
            raise ConfigInvalid(f"candidate {name} price is not a number") from exc
        if d < 0:
            raise ConfigInvalid(f"candidate {name} price is negative")
        return d

    return Price(
        money(raw.get("input_usd_per_mtok")),
        money(raw.get("output_usd_per_mtok")),
        str(raw["source"]),
    )


def _candidate(name: str, raw: Any) -> Candidate:
    if not isinstance(raw, Mapping):
        raise ConfigInvalid(f"candidate {name} must be a mapping")
    adapter = str(raw.get("adapter"))
    if adapter not in ADAPTERS:
        raise ConfigInvalid(f"candidate {name} has an unknown adapter")
    code = str(raw.get("provider_code", ""))
    if not code:
        raise ConfigInvalid(f"candidate {name} needs a provider_code")
    window = _fact(raw.get("context_window"), f"{name}.context_window")
    if window is not None and (
        isinstance(window, bool) or not isinstance(window, int) or window < 1
    ):
        raise ConfigInvalid(f"candidate {name} context_window must be a positive integer")
    classes = _fact(raw.get("data_classes"), f"{name}.data_classes")
    if not isinstance(classes, list) or not classes or not set(classes) <= DATA_CLASSES:
        raise ConfigInvalid(f"candidate {name} declares no or unknown data classes")
    retention = _fact(raw.get("zero_retention"), f"{name}.zero_retention")
    caching = _fact(raw.get("caching"), f"{name}.caching")
    for label, v in (("zero_retention", retention), ("caching", caching)):
        if v is not None and not isinstance(v, bool):
            raise ConfigInvalid(f"candidate {name} {label} must be a boolean or null")
    model = _fact(raw.get("model"), f"{name}.model")
    family = _fact(raw.get("family"), f"{name}.family")
    if not isinstance(model, str) or not model or not isinstance(family, str) or not family:
        raise ConfigInvalid(f"candidate {name} needs a model and a family")
    return Candidate(
        name,
        adapter,
        code,
        family,
        model,
        window,
        frozenset(classes),
        retention,
        caching,
        _price(raw.get("price"), name),
    )


def build(data: Mapping[str, Any], raw: bytes) -> RoutingConfig:
    """Validate a parsed mapping (``ConfigInvalid`` on anything wrong)."""
    try:
        candidates = {str(n): _candidate(str(n), c) for n, c in data["candidates"].items()}
        tiers_raw = _section(data, "tiers")
        tiers: dict[str, Tier] = {}
        for name, t in tiers_raw["value"].items():
            ladder = tuple(str(x) for x in t.get("ladder") or [])
            if not ladder:
                raise ConfigInvalid(f"tier {name} has no candidate")
            unknown = set(ladder) - candidates.keys()
            if unknown:
                raise ConfigInvalid(f"tier {name} names unknown candidates {sorted(unknown)}")
            after = t.get("fallback_after_retryable", 1)
            if isinstance(after, bool) or not isinstance(after, int) or after < 1:
                raise ConfigInvalid(f"tier {name} fallback_after_retryable must be >= 1")
            tiers[str(name)] = Tier(str(name), ladder, after)
        judge_tier = str(tiers_raw.get("judge_tier", ""))
        if judge_tier not in tiers:
            raise ConfigInvalid("judge_tier must name a tier")
        kinds: dict[str, TaskKind] = {}
        for name, k in _section(data, "task_kinds")["value"].items():
            tier = str(k.get("tier"))
            if tier not in tiers:
                raise ConfigInvalid(f"task kind {name} names an unknown tier")
            mt = k.get("max_tokens")
            if isinstance(mt, bool) or not isinstance(mt, int) or mt < 1:
                raise ConfigInvalid(f"task kind {name} needs a positive max_tokens")
            temp = k.get("temperature")
            if temp is not None and (isinstance(temp, bool) or not isinstance(temp, int | float)):
                raise ConfigInvalid(f"task kind {name} temperature must be a number or null")
            kinds[str(name)] = TaskKind(
                str(name),
                tier,
                mt,
                None if temp is None else float(temp),
                bool(k.get("cacheable", False)),
            )
        caps_raw = _section(data, "token_caps")["value"]
        caps = {str(k): (None if v is None else int(v)) for k, v in caps_raw.items()}
        health = _section(data, "health")
        floor = _fact(data.get("cache_read_ratio_floor"), "cache_read_ratio_floor")
        reasks = _fact(data.get("schema_reasks"), "schema_reasks")
        if not isinstance(reasks, int) or reasks < 0 or reasks > 1:
            raise ConfigInvalid("schema_reasks must be 0 or 1 (one bounded re-ask)")
        report = _fact(data.get("benchmark_report"), "benchmark_report")
        return RoutingConfig(
            version=str(data["version"]),
            routing_version=str(data["routing_version"]),
            task_kinds=kinds,
            tiers=tiers,
            judge_tier=judge_tier,
            candidates=candidates,
            token_caps=caps,
            health_threshold=int(health["failure_threshold"]),
            health_window_s=float(health["window_s"]),
            health_cooldown_s=float(health["cooldown_s"]),
            cache_floor=float(floor),
            schema_reasks=reasks,
            benchmark_report=None if report is None else str(report),
            config_hash=hashlib.sha256(raw).hexdigest(),
        )
    except ConfigInvalid:
        raise
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ConfigInvalid(f"invalid routing configuration: {exc!r}") from exc


def config_dir() -> Path:
    """The repository's ``config`` directory."""
    return Path(os.environ.get("CONFIG_DIR") or Path(__file__).resolve().parents[2] / "config")


SPLIT_FROM, SPLIT_TO = "extractor", "planner"


def with_model_split(cfg: RoutingConfig, mode: str) -> RoutingConfig:
    """The admin console's Model split (platform defaults). ``split`` keeps the configured tiers
    (the lower cost model for pulls and extraction, the stronger model for analysis); ``single``
    sends the extractor tier's task kinds to the planner tier, so one model does both. The judge
    tier is never changed (its independence from the generator stays, R6), and the routing
    version changes with the mode so a recorded reply is never reused across modes."""
    if mode == "split":
        return cfg
    if mode != "single":
        raise ValueError(f"unknown model split {mode!r}")
    kinds = {
        name: replace(kind, tier=SPLIT_TO) if kind.tier == SPLIT_FROM else kind
        for name, kind in cfg.task_kinds.items()
    }
    return replace(cfg, task_kinds=kinds, routing_version=f"{cfg.routing_version}+single")


@lru_cache(maxsize=4)
def load_routing_config(directory: Path | None = None) -> RoutingConfig:
    """Load and validate ``model_routing.yaml``."""
    path = (directory or config_dir()) / "model_routing.yaml"
    try:
        raw = path.read_bytes()
        data = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigInvalid(f"cannot load {path.name}") from exc
    if not isinstance(data, Mapping):
        raise ConfigInvalid("model_routing.yaml must contain a mapping")
    return build(data, raw)
