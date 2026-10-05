"""Load and validate grading configuration; record content hashes with the run.

Sources of numbers (no constant in code):
* ``config/grading.yaml`` (identical to the skill's ``assets/grading.yaml``): tier weights,
  half-lives, grade thresholds (parsed strictly from the rule strings), the Lead rule,
  statement policy and eval gates.
* ``config/grading_policy.yaml``: proposals awaiting decisions (D-01 and D-11 extensions,
  D-14, similarity). Every block carries ``status: proposal``.
* ``config/source_registry.yaml``: D-15 proposal.
* ``config/routing.yaml``: ``tool_categories``.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from account_intelligence.config import CONFIG_DIR
from account_intelligence.grading.errors import ConfigError

Tier = Literal["T1", "T2", "T3", "T4"]
TIERS: tuple[Tier, ...] = ("T1", "T2", "T3", "T4")
ProductGrade = Literal["confirmed", "probable", "hypothesis", "lead", "internal"]
EngineGrade = Literal["A", "B", "C", "D", "CONFLICTING", "BLOCKED"]
Origin = Literal["full_page_read", "cached_copy", "search_snippet", "feed_item", "broker_record"]
Normaliser = Literal["casefold_text", "number", "iso_date", "opaque"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Thresholds(_M):
    """Engine grade thresholds as Decimals (F4)."""

    a: Decimal
    a_min_groups: int
    a_required_tiers: tuple[Tier, ...]
    b: Decimal
    c: Decimal
    d_below: Decimal
    conflict_above: Decimal


class LeadRule(_M):
    """The Lead rule numbers: distinct queries and tool categories."""

    min_distinct_queries: int = Field(ge=1)
    min_distinct_tool_categories: int = Field(ge=1)


class EvalGates(_M):
    """Calibration gates from assets/grading.yaml."""

    precision_grade_A_min: float
    precision_grade_B_min: float
    coverage_expected_attributes_min: float
    citation_validity_min: float
    calibration_error_max: float
    injection_and_leakage_pass: float


class Cap(_M):
    """A grade cap applied after precedence (judge, lineage, dating)."""

    engine_grade: EngineGrade
    flag: str
    product_grade: ProductGrade | None = None


class Precedence(_M):
    """Product grade precedence (D-01 proposal)."""

    status: Literal["proposal", "decided"]
    decision: str
    order: tuple[str, ...]
    conflicting_max_product_grade: ProductGrade
    blocked_product_grade: ProductGrade
    internal_authoritative_default: bool


class JudgeCfg(_M):
    """Judge settings: which task is the judge, which the generator, and the re-ask cap."""

    status: Literal["proposal", "decided"]
    decision: str
    required_for_engine_grades: tuple[EngineGrade, ...]
    required_for_product_grades: tuple[ProductGrade, ...]
    max_reasks: int = Field(ge=0, le=3)
    unavailable_cap: Cap
    judge_task: str
    generator_task: str


class SimilarityCfg(_M):
    """The differently-worded detector: stopwords, threshold and the rejected-proposal cap."""

    status: Literal["proposal", "decided"]
    source: str
    metric: Literal["jaccard_token_set"]
    max_similarity_exclusive: float = Field(gt=0, le=1)
    stopwords: tuple[str, ...]
    max_rejected_proposals: int = Field(ge=1)
    max_repass: int = Field(ge=0)


class SpansCfg(_M):
    """Supporting-sentence bounds and the canary and instruction markers (G4, G14)."""

    status: Literal["proposal", "decided"]
    min_sentence_chars: int
    max_sentence_chars: int
    max_value_chars: int
    max_reasoning_chars: int
    max_question_chars: int
    max_excerpt_chars: int
    canary_markers: tuple[str, ...]
    instruction_markers: tuple[str, ...]


class RegistryEntry(_M):
    """One source domain's tier, aggregator flag and lineage group (D-15)."""

    domain: str
    tier: Tier
    is_aggregator: bool
    lineage_group: str
    live_state: bool
    source: str
    lineage_known: bool = True


class ProviderEntry(_M):
    """A provider code's tier and lineage (never a vendor name)."""

    tier: Tier
    is_aggregator: bool
    lineage_group: str
    lineage_known: bool
    source: str


class Registry(_M):
    """The source registry; unlisted domains default to T4 and are flagged."""

    unlisted_tier: Tier
    unlisted_is_aggregator: bool
    unlisted_flag: str
    account_tier: Tier
    account_lineage_group: str
    domains: tuple[RegistryEntry, ...]
    provider_codes: dict[str, ProviderEntry]

    @model_validator(mode="after")
    def _no_vendor_names(self) -> Registry:
        for code in self.provider_codes:
            if not re.fullmatch(r"p_[a-z]+", code):
                raise ValueError("provider registry keys must be provider codes (p_...)")
        return self


class GradingConfig(_M):
    """The validated grading configuration and its content hash (pinned on the run)."""

    version: str
    weights: dict[Tier, float]
    half_lives: dict[str, float]
    thresholds: Thresholds
    lead_rule: LeadRule
    may_state: dict[ProductGrade, bool]
    requires_question: tuple[ProductGrade, ...]
    eval_gates: EvalGates
    precedence: Precedence
    judge: JudgeCfg
    lineage_cap: Cap
    date_cap: Cap
    origin_by_operation: dict[str, Origin]
    category_by_operation: dict[str, str]
    tool_categories: tuple[str, ...]
    attribute_classes: dict[str, str]
    normalisers: dict[str, Normaliser]
    similarity: SimilarityCfg
    spans: SpansCfg
    confidence_decimals: int
    registry: Registry
    content_hash: str

    @model_validator(mode="after")
    def _consistent(self) -> GradingConfig:
        if set(self.weights) != set(TIERS):
            raise ValueError("weights must define exactly T1..T4")
        for w in self.weights.values():
            if not 0 < w < 1:
                raise ValueError("tier weights must be in (0, 1)")
        for name, hl in self.half_lives.items():
            if hl <= 0:
                raise ValueError(f"half-life {name} must be positive")
        for prefix, cls in self.attribute_classes.items():
            if cls not in self.half_lives:
                raise ValueError(f"attribute prefix {prefix} maps to unknown class {cls}")
        for op, cat in self.category_by_operation.items():
            if cat not in self.tool_categories:
                raise ValueError(f"operation {op} maps to unknown tool category {cat}")
        return self


# Strict patterns for the rule strings in grading.yaml. A wording change fails loudly.
_A_RULE = re.compile(
    r"^c >= ([0-9.]+) and >= (\d+) independent lineage groups, one of them (T\d) or (T\d)$"
)
_GE_RULE = re.compile(r"^c >= ([0-9.]+)$")
_LT_RULE = re.compile(r"^c < ([0-9.]+)$")
_CONFLICT = re.compile(r"both c > ([0-9.]+)")


def _match(pattern: re.Pattern[str], text: str, what: str) -> re.Match[str]:
    m = pattern.search(text)
    if m is None:
        raise ConfigError(f"cannot parse {what}: {text!r}")
    return m


def _read(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"cannot read {path.name}") from error


def _thresholds(engine: dict[str, Any]) -> Thresholds:
    g = engine["grades"]
    a = _match(_A_RULE, g["A"]["rule"], "grade A rule")
    return Thresholds(
        a=Decimal(a.group(1)),
        a_min_groups=int(a.group(2)),
        a_required_tiers=(a.group(3), a.group(4)),
        b=Decimal(_match(_GE_RULE, g["B"]["rule"], "grade B rule").group(1)),
        c=Decimal(_match(_GE_RULE, g["C"]["rule"], "grade C rule").group(1)),
        d_below=Decimal(_match(_LT_RULE, g["D"]["rule"], "grade D rule").group(1)),
        conflict_above=Decimal(
            _match(_CONFLICT, engine["conflicting"], "conflicting rule").group(1)
        ),
    )


def _proposal(block: dict[str, Any], name: str) -> dict[str, Any]:
    if block.get("status") not in ("proposal", "decided"):
        raise ConfigError(f"{name} must say status: proposal or decided")
    return {k: v for k, v in block.items() if k not in ("status", "decision", "source")}


def load_grading_config(config_dir: Path | None = None) -> GradingConfig:
    """Load, validate and hash all grading configuration. Raises ``ConfigError``."""
    root = config_dir or CONFIG_DIR
    grading = _read(root / "grading.yaml")
    policy = _read(root / "grading_policy.yaml")
    registry = _read(root / "source_registry.yaml")
    routing = _read(root / "routing.yaml")
    try:
        engine = grading["engine"]
        weights = {
            t.split("_", 1)[0]: float(v["weight"]) for t, v in engine["source_tiers"].items()
        }
        product = grading["product_grades"]
        attrs = policy["attributes"]
        cfg = GradingConfig(
            version=str(grading["version"]),
            weights=weights,
            half_lives={k: float(v) for k, v in engine["half_life_days"].items()},
            thresholds=_thresholds(engine),
            lead_rule=LeadRule(**product["lead"]["stays_lead_only_if"]),
            may_state={k: bool(v["may_state_to_prospect"]) for k, v in product.items()},
            requires_question=tuple(
                k for k, v in product.items() if v.get("requires_confirming_question")
            ),
            eval_gates=EvalGates(**engine["eval_gates"]),
            precedence=Precedence(**policy["product_precedence"]),
            judge=JudgeCfg(**policy["judge"]),
            lineage_cap=Cap(**policy["lineage"]["unknown_group_reaching_a_cap"]),
            date_cap=Cap(**policy["dating"]["unknown_date_cap"]),
            origin_by_operation=_proposal(policy["origin_by_operation"], "origin_by_operation"),
            category_by_operation=_proposal(
                policy["tool_category_by_operation"], "tool_category_by_operation"
            ),
            tool_categories=tuple(routing["tool_categories"]),
            attribute_classes=attrs["classes"],
            normalisers=attrs["normalisers"],
            similarity=SimilarityCfg(**policy["lead_similarity"]),
            spans=SpansCfg(**policy["spans"]),
            confidence_decimals=int(policy["numeric"]["confidence_decimals"]),
            registry=Registry(
                unlisted_tier=registry["unlisted_domain"]["tier"],
                unlisted_is_aggregator=registry["unlisted_domain"]["is_aggregator"],
                unlisted_flag=registry["unlisted_domain"]["flag"],
                account_tier=registry["account_primary"]["tier"],
                account_lineage_group=registry["account_primary"]["lineage_group"],
                domains=tuple(RegistryEntry(**d) for d in registry.get("domains") or ()),
                provider_codes={
                    k: ProviderEntry(**v) for k, v in (registry.get("provider_codes") or {}).items()
                },
            ),
            content_hash=_hash(grading, policy, registry, routing.get("tool_categories")),
        )
    except (KeyError, TypeError, ValidationError) as error:
        raise ConfigError(f"invalid grading configuration: {error}") from error
    return cfg


def _hash(*parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
