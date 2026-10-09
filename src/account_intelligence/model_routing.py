"""Centralized backend automatic model router and runtime governance (Phase 5).

Enforces:
1. Strict model allowlist:
   - ``claude-opus-5-5``: Phase 1 Data Collection and judgment calls
   - ``claude-fable-5-1``: Phase 2 Prediction, Analysis, and Follow-Up Chat
   All models outside this allowlist are strictly rejected; no silent substitutions.
2. Server-side automatic routing: users and API clients cannot override model selection.
3. Centralized pricing and token cost calculation from ``config/models.yaml``.
4. Raw artifact isolation under ``tenants/{tenant}/runs/{run_id}/raw/``.
5. Fresh session separation for Phase 2: Fable has no access to live web tools.
6. Runtime quality validation checks and explicit retry/hold policies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import yaml

from account_intelligence.config import CONFIG_DIR
from obs.logging import logger

_log = logger(__name__)

# Hard allowlist of approved model identifiers
APPROVED_MODELS = frozenset({"claude-opus-5-5", "claude-fable-5-1"})


class ModelRoutingError(RuntimeError):
    """Raised when an invalid model or disallowed model override is attempted."""


class PhaseLimitBreached(RuntimeError):
    """Raised when a phase's turn or spending cap is exceeded."""


class QualityCheckFailed(RuntimeError):
    """Raised when completed artifacts fail runtime quality verification."""


class PipelinePhase(str, Enum):
    COLLECTION = "phase_1_collection"
    ANALYSIS = "phase_2_analysis"
    QUALITY_CHECKS = "phase_3_quality_checks"
    CHAT = "follow_up_chat"


class PhaseFailureAction(str, Enum):
    RETRY = "retry_same_model"
    HOLD = "hold_run_for_review"



@dataclass(frozen=True)
class ModelPricing:
    """Token pricing in USD per 1M tokens."""

    input_usd_per_million: Decimal
    cached_read_usd_per_million: Decimal
    cache_write_usd_per_million: Decimal
    output_usd_per_million: Decimal


@dataclass(frozen=True)
class ModelDefinition:
    """Configured metadata and pricing for an allowlisted model."""

    id: str
    display_name: str
    responsibility: str
    pricing: ModelPricing
    context_window: int
    max_output_tokens: int


@dataclass(frozen=True)
class PhasePolicy:
    """Enforced execution constraints for a pipeline phase."""

    phase: PipelinePhase
    model_id: str
    turn_limit: int
    spend_limit_usd: Decimal
    fresh_session: bool = False
    allow_collection_tools: bool = True
    gcs_raw_layout: str = "tenants/{tenant}/runs/{run_id}/raw/"


@dataclass(frozen=True)
class ModelCallUsage:
    """Reported token usage for a single model call."""

    input_tokens: int
    output_tokens: int
    cached_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class QualityCheckResult:
    """Verdict of Phase 3 automated quality checks."""

    passed: bool
    held_for_review: bool
    reasons: list[str] = field(default_factory=list)


class ModelRouter:
    """Centralized router selecting models automatically per phase."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._path = config_path or (CONFIG_DIR / "models.yaml")
        self._models: dict[str, ModelDefinition] = {}
        self._policies: dict[PipelinePhase, PhasePolicy] = {}
        self._load_config()

    def _load_config(self) -> None:
        raw = yaml.safe_load(self._path.read_text())
        raw_models = raw.get("models", {})
        for mid, mdata in raw_models.items():
            if mid not in APPROVED_MODELS:
                continue
            pricing_data = mdata.get("pricing", {})
            pricing = ModelPricing(
                input_usd_per_million=Decimal(str(pricing_data.get("input_usd_per_million", "0"))),
                cached_read_usd_per_million=Decimal(str(pricing_data.get("cached_read_usd_per_million", "0"))),
                cache_write_usd_per_million=Decimal(str(pricing_data.get("cache_write_usd_per_million", "0"))),
                output_usd_per_million=Decimal(str(pricing_data.get("output_usd_per_million", "0"))),
            )
            self._models[mid] = ModelDefinition(
                id=mid,
                display_name=str(mdata.get("display_name", mid)),
                responsibility=str(mdata.get("responsibility", "")),
                pricing=pricing,
                context_window=int(mdata.get("context_window", 1_000_000)),
                max_output_tokens=int(mdata.get("max_output_tokens", 128_000)),
            )

        # Configure pipeline phase policies
        self._policies[PipelinePhase.COLLECTION] = PhasePolicy(
            phase=PipelinePhase.COLLECTION,
            model_id="claude-opus-5-5",
            turn_limit=30,
            spend_limit_usd=Decimal("5.00"),
            fresh_session=False,
            allow_collection_tools=True,
            gcs_raw_layout="tenants/{tenant}/runs/{run_id}/raw/",
        )
        self._policies[PipelinePhase.ANALYSIS] = PhasePolicy(
            phase=PipelinePhase.ANALYSIS,
            model_id="claude-fable-5-1",
            turn_limit=20,
            spend_limit_usd=Decimal("5.00"),
            fresh_session=True,
            allow_collection_tools=False,
            gcs_raw_layout="tenants/{tenant}/runs/{run_id}/raw/",
        )
        self._policies[PipelinePhase.CHAT] = PhasePolicy(
            phase=PipelinePhase.CHAT,
            model_id="claude-fable-5-1",
            turn_limit=20,
            spend_limit_usd=Decimal("2.00"),
            fresh_session=True,
            allow_collection_tools=False,
        )

    def select_model(self, phase: PipelinePhase) -> ModelDefinition:
        """Automatically select the required model for the phase. Callers cannot override."""
        policy = self._policies.get(phase)
        if policy is None:
            raise ModelRoutingError(f"unknown pipeline phase: {phase}")
        model = self._models.get(policy.model_id)
        if model is None:
            raise ModelRoutingError(f"configured model {policy.model_id} is unavailable")
        return model

    def validate_model_id(self, model_id: str) -> None:
        """Hard validation rejecting any model outside the allowlist."""
        if model_id not in APPROVED_MODELS:
            raise ModelRoutingError(
                f"Model {model_id!r} is not on the allowlist. Only {sorted(APPROVED_MODELS)} are permitted."
            )

    def calculate_cost(self, model_id: str, usage: ModelCallUsage) -> Decimal:
        """Centralized token cost calculation accounting for uncached, cached, and output tokens."""
        self.validate_model_id(model_id)
        model = self._models[model_id]
        p = model.pricing
        million = Decimal(1_000_000)

        input_cost = (Decimal(usage.input_tokens) * p.input_usd_per_million) / million
        cached_read_cost = (Decimal(usage.cached_read_tokens) * p.cached_read_usd_per_million) / million
        cache_write_cost = (Decimal(usage.cache_write_tokens) * p.cache_write_usd_per_million) / million
        output_cost = (Decimal(usage.output_tokens) * p.output_usd_per_million) / million

        return input_cost + cached_read_cost + cache_write_cost + output_cost

    def raw_artifact_path(self, tenant_id: UUID | str, run_id: UUID | str, filename: str) -> str:
        """Tenant-isolated GCS object layout for raw collection results."""
        clean_file = filename.lstrip("/")
        return f"tenants/{tenant_id}/runs/{run_id}/raw/{clean_file}"

    def check_limits(self, phase: PipelinePhase, turns_used: int, spend_used_usd: Decimal) -> None:
        """Enforce per-phase turn and spending limits."""
        policy = self._policies[phase]
        if turns_used >= policy.turn_limit:
            raise PhaseLimitBreached(f"Phase {phase.value} exceeded turn limit of {policy.turn_limit}")
        if spend_used_usd >= policy.spend_limit_usd:
            raise PhaseLimitBreached(f"Phase {phase.value} exceeded spend ceiling of ${policy.spend_limit_usd}")

    def execute_quality_checks(
        self,
        tenant_id: UUID,
        run_id: UUID,
        artifacts: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> QualityCheckResult:
        """Phase 3 runtime verification. On failure, holds the run for human review."""
        reasons: list[str] = []

        # 1. Source traceability: check that citations link back to real sources
        cited_codes = artifacts.get("cited_source_codes", [])
        known_codes = {s.get("source_code") for s in sources if s.get("source_code")}
        missing_sources = [c for c in cited_codes if c not in known_codes]
        if missing_sources:
            reasons.append(f"Missing source citations: {missing_sources}")

        # 2. Prohibited tool / vendor names in customer deliverables
        prohibited_vendors = {"parallel", "firecrawl", "predictleads", "bitscale", "sumble"}
        report_text = str(artifacts.get("report_content", "")).lower()
        for vendor in prohibited_vendors:
            if vendor in report_text:
                reasons.append(f"Prohibited vendor name {vendor!r} appeared in report output")

        # 3. Tenant cross-contamination
        artifact_tenant = artifacts.get("tenant_id")
        if artifact_tenant and str(artifact_tenant) != str(tenant_id):
            reasons.append("Tenant isolation violation detected in artifact metadata")

        # 4. Required output deliverables
        required_outputs = artifacts.get("deliverables", [])
        if not required_outputs:
            reasons.append("Deliverables bundle is empty")

        if reasons:
            _log.warning(
                "quality_checks.held_for_review",
                extra={
                    "event": "quality_checks.held_for_review",
                    "tenant_id": str(tenant_id),
                    "run_id": str(run_id),
                    "reasons": reasons,
                },
            )
            return QualityCheckResult(passed=False, held_for_review=True, reasons=reasons)

        return QualityCheckResult(passed=True, held_for_review=False)

    def handle_phase_failure(
        self,
        phase: PipelinePhase,
        attempt: int,
        error: Exception,
    ) -> PhaseFailureAction:
        """On Phase 1 (Opus) failure: retry once, then hold the run. Never silently fall back."""
        policy = self._policies[phase]
        _log.warning(
            "model_routing.phase_failure",
            extra={
                "event": "model_routing.phase_failure",
                "phase": phase.value,
                "model": policy.model_id,
                "attempt": attempt,
                "error": str(error),
            },
        )
        if attempt <= 1:
            return PhaseFailureAction.RETRY
        return PhaseFailureAction.HOLD

    def log_call_usage(
        self,
        tenant_id: UUID | str,
        account_id: UUID | str | None,
        phase: PipelinePhase,
        model_id: str,
        usage: ModelCallUsage,
        cost_usd: Decimal,
    ) -> None:
        """Log tokens and cost per call tagged tenant / account / phase / model."""
        _log.info(
            "model_routing.call_usage",
            extra={
                "event": "model_routing.call_usage",
                "tenant": str(tenant_id),
                "account": str(account_id) if account_id else None,
                "phase": phase.value,
                "model": model_id,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cached_read_tokens": usage.cached_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
                "cost_usd": str(cost_usd),
            },
        )

    def cached_prefix_block(self, content: str) -> dict[str, Any]:
        """Wrap prefix text (skill, rules, checklist) in an Anthropic ephemeral cache control block."""
        return {
            "type": "text",
            "text": content,
            "cache_control": {"type": "ephemeral"},
        }

    def get_inference_region(self, tenant_industry: str | None) -> str:
        """US-only inference routing for healthcare tenants; default region for other tenants."""
        if tenant_industry and tenant_industry.lower() in {
            "healthcare", "health", "medical", "pharma", "biotech", "life sciences",
        }:
            return "us-central1"
        return "us-central1"

    @property
    def workspace_retention_days(self) -> int:
        """30-day retention on the production workspace."""
        return 30



_router_instance: ModelRouter | None = None


def get_model_router() -> ModelRouter:
    """Access the cached singleton router."""
    global _router_instance
    if _router_instance is None:
        _router_instance = ModelRouter()
    return _router_instance
