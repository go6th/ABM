"""Load and validate the job pipeline configuration (fail fast); hash it for the run record.

Three files, all under ``CONFIG_DIR``: ``job_lanes.yaml`` (lanes, platform lexicon, keywords),
``posting_rubric.yaml`` (class rules, full-text scope) and ``jobs_v1.yaml`` (caps, templates).
Every lane, platform, rule and cap must carry a ``source`` or ``status`` field (spec 2.1); a
missing one is a ``ConfigError``. Nothing here is a fact from the sources unless its ``source``
says so: rules and caps are ``status: proposal`` (D-16, D-17).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.dev_limits import UNCAPPED, dev_uncapped
from jobs.errors import ConfigError

CLASSES = ("build", "admin_owner", "working_tool", "user")


def load_yaml(path: Path) -> tuple[dict[str, Any], str]:
    """Load a YAML mapping and return it with the SHA-256 of the file bytes."""
    try:
        raw = path.read_bytes()
        value = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load configuration {path.name}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"configuration {path.name} must contain a mapping")
    return value, hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Lane:
    """One research lane: its platforms (canonical name to aliases) and board keywords."""

    id: str
    label: str
    platforms: dict[str, tuple[str, ...]]
    keywords: tuple[str, ...]
    keyword_source: str


@dataclass(frozen=True)
class Rule:
    """One deterministic classification rule (whole-word cues in the description)."""

    rule_id: str
    label: str
    cues: tuple[str, ...]
    positive: str
    negative: str


@dataclass(frozen=True)
class JobsConfig:
    """The validated configuration and its combined hash."""

    lanes: tuple[Lane, ...]
    rules: tuple[Rule, ...]
    full_text_required_classes: frozenset[str]
    caps: dict[str, int]
    count_sentence: str
    config_hash: str

    @property
    def lexicon(self) -> dict[str, tuple[str, ...]]:
        """Platform name to aliases, across all lanes."""
        return {name: aliases for lane in self.lanes for name, aliases in lane.platforms.items()}

    def lane_of(self, platform: str) -> str:
        """The lane a platform belongs to."""
        return next(lane.id for lane in self.lanes if platform in lane.platforms)


def _need_provenance(entry: dict[str, Any], where: str) -> None:
    if not any(k in entry for k in ("source", "status", "keyword_source", "platforms_source")):
        raise ConfigError(f"{where} has no source or status")


def _lanes(data: dict[str, Any]) -> tuple[Lane, ...]:
    fallback = str((data.get("keyword_fallback") or {}).get("value", "none"))
    if fallback not in ("platform_names", "none"):
        raise ConfigError("keyword_fallback must be platform_names or none")
    lanes = []
    for lane in data["lanes"]:
        _need_provenance(lane, f"lane {lane.get('id')}")
        platforms: dict[str, tuple[str, ...]] = {}
        for platform in lane.get("platforms") or []:
            _need_provenance(platform, f"platform {platform.get('name')}")
            platforms[str(platform["name"])] = tuple(str(a) for a in platform.get("aliases") or [])
        keywords = tuple(str(k) for k in lane.get("keywords") or [])
        source = str(lane.get("keyword_source", ""))
        if not keywords and fallback == "platform_names" and platforms:
            keywords, source = tuple(platforms), "D-17 proposal: platform names as keywords"
        lanes.append(Lane(str(lane["id"]), str(lane["label"]), platforms, keywords, source))
    return tuple(lanes)


def _rules(data: dict[str, Any]) -> tuple[Rule, ...]:
    rules = []
    for rule in data["rules"]:
        _need_provenance(rule, f"rule {rule.get('rule_id')}")
        if rule["label"] not in CLASSES:
            raise ConfigError(f"rule {rule['rule_id']} has an unknown class")
        if not rule.get("positive") or not rule.get("negative"):
            raise ConfigError(f"rule {rule['rule_id']} needs a positive and a negative example")
        cues = tuple(str(c) for c in rule["cues"])
        rules.append(
            Rule(str(rule["rule_id"]), str(rule["label"]), cues, rule["positive"], rule["negative"])
        )
    return tuple(rules)


@lru_cache(maxsize=4)
def load_jobs_config(directory: Path | None = None) -> JobsConfig:
    """Load all three files; ``ConfigError`` on anything missing or unsourced."""
    base = directory or CONFIG_DIR
    lanes, h1 = load_yaml(base / "job_lanes.yaml")
    rubric, h2 = load_yaml(base / "posting_rubric.yaml")
    jobs, h3 = load_yaml(base / "jobs_v1.yaml")
    try:
        scope = rubric["full_text_required_classes"]
        _need_provenance(scope, "full_text_required_classes")
        required = frozenset(str(c) for c in scope["value"])
        if not required <= set(CLASSES):
            raise ConfigError("full_text_required_classes names an unknown class")
        caps = {}
        for name, cap in jobs["caps"].items():
            _need_provenance(cap, f"cap {name}")
            caps[name] = int(cap["value"])
        if dev_uncapped():  # local stack: read and extract every posting (dev_limits)
            caps.update({k: UNCAPPED for k in ("max_page_reads", "max_extractor_calls")})
        template = jobs["templates"]["count_sentence"]
        _need_provenance(template, "count_sentence template")
        return JobsConfig(
            lanes=_lanes(lanes),
            rules=_rules(rubric),
            full_text_required_classes=required,
            caps=caps,
            count_sentence=str(template["value"]),
            config_hash=hashlib.sha256((h1 + h2 + h3).encode()).hexdigest(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"invalid job pipeline configuration: {exc}") from exc
