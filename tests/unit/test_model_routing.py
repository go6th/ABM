"""The phase model router reads every model id and allowlist entry from config/models.yaml
(R1: no model id in code) and refuses a model off the allowlist."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.model_routing import ModelRouter, ModelRoutingError, PipelinePhase

RAW = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())


def test_phase_models_come_from_config() -> None:
    router = ModelRouter()
    assert router.approved_models == frozenset(RAW["allowlist"])
    for phase in (PipelinePhase.COLLECTION, PipelinePhase.ANALYSIS, PipelinePhase.CHAT):
        assert router.select_model(phase).id == RAW["routing"][phase.value]["model"]
    with pytest.raises(ModelRoutingError):
        router.select_model(PipelinePhase.QUALITY_CHECKS)  # a validator, not a model


def test_a_model_off_the_allowlist_is_refused(tmp_path: Path) -> None:
    edited = dict(RAW, allowlist=[RAW["allowlist"][0]])
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(edited))
    router = ModelRouter(path)
    with pytest.raises(ModelRoutingError):
        router.validate_model_id(RAW["allowlist"][1])
    router.validate_model_id(RAW["allowlist"][0])
