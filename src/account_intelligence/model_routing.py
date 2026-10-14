"""The phase model router: which allowlisted model a pipeline phase uses (Phase 5).

1. Strict model allowlist (``allowlist`` in ``config/models.yaml``); each phase's model comes
   from that file's ``routing`` section. Model ids appear only in configuration (R1). A model
   outside the allowlist is rejected; no silent substitutions.
2. Server-side automatic routing: users and API clients cannot override model selection.

Costs, limits and quality checks live with the run (``runs``, ``spend``, ``grading``), not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml

from account_intelligence.config import CONFIG_DIR


class ModelRoutingError(RuntimeError):
    """Raised when an invalid model or disallowed model override is attempted."""


class PipelinePhase(StrEnum):
    """The run phase a model call belongs to; each phase has its own model."""

    COLLECTION = "phase_1_collection"
    ANALYSIS = "phase_2_analysis"
    QUALITY_CHECKS = "phase_3_quality_checks"
    CHAT = "follow_up_chat"


@dataclass(frozen=True)
class ModelDefinition:
    """Configured metadata of an allowlisted model."""

    id: str
    display_name: str
    responsibility: str
    context_window: int
    max_output_tokens: int


class ModelRouter:
    """Centralized router selecting models automatically per phase."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._path = config_path or (CONFIG_DIR / "models.yaml")
        self._models: dict[str, ModelDefinition] = {}
        self._phase_models: dict[PipelinePhase, str] = {}
        self.approved_models: frozenset[str] = frozenset()
        self._load_config()

    def _load_config(self) -> None:
        raw = yaml.safe_load(self._path.read_text())
        self.approved_models = frozenset(str(m) for m in raw.get("allowlist", []))
        for mid, mdata in (raw.get("models") or {}).items():
            if mid not in self.approved_models:
                continue
            self._models[mid] = ModelDefinition(
                id=mid,
                display_name=str(mdata.get("display_name", mid)),
                responsibility=str(mdata.get("responsibility", "")),
                context_window=int(mdata.get("context_window", 1_000_000)),
                max_output_tokens=int(mdata.get("max_output_tokens", 128_000)),
            )
        # A phase without a model (the quality checks) has no entry here.
        for phase in PipelinePhase:
            spec = (raw.get("routing") or {}).get(phase.value) or {}
            if "model" in spec:
                self._phase_models[phase] = str(spec["model"])

    def select_model(self, phase: PipelinePhase) -> ModelDefinition:
        """Automatically select the required model for the phase. Callers cannot override."""
        model_id = self._phase_models.get(phase)
        if model_id is None:
            raise ModelRoutingError(f"unknown pipeline phase: {phase}")
        model = self._models.get(model_id)
        if model is None:
            raise ModelRoutingError(f"configured model {model_id} is unavailable")
        return model

    def validate_model_id(self, model_id: str) -> None:
        """Hard validation rejecting any model outside the allowlist."""
        if model_id not in self.approved_models:
            raise ModelRoutingError(
                f"Model {model_id!r} is not on the allowlist. Only "
                f"{sorted(self.approved_models)} are permitted."
            )


_router_instance: ModelRouter | None = None


def get_model_router() -> ModelRouter:
    """Access the cached singleton router."""
    global _router_instance
    if _router_instance is None:
        _router_instance = ModelRouter()
    return _router_instance
