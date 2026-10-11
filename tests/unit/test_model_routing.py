"""The phase model router reads every model id, limit and allowlist entry from config/models.yaml
(R1: no model id in code) and scans report text with the client-output denylist."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.connectors.registry_internal import vendor_names
from account_intelligence.connectors.settings import load_providers_config
from account_intelligence.model_routing import (
    ModelCallUsage,
    ModelRouter,
    ModelRoutingError,
    PhaseLimitBreached,
    PipelinePhase,
)

RAW = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())


def test_phase_models_and_limits_come_from_config() -> None:
    router = ModelRouter()
    assert router.approved_models == frozenset(RAW["allowlist"])
    for phase in (PipelinePhase.COLLECTION, PipelinePhase.ANALYSIS, PipelinePhase.CHAT):
        spec = RAW["routing"][phase.value]
        assert router.select_model(phase).id == spec["model"]
        with pytest.raises(PhaseLimitBreached):
            router.check_limits(phase, int(spec["turn_limit"]), Decimal(0))
    with pytest.raises(ModelRoutingError):
        router.select_model(PipelinePhase.QUALITY_CHECKS)  # a validator, not a model


def test_a_model_off_the_allowlist_is_refused(tmp_path: Path) -> None:
    edited = dict(RAW, allowlist=[RAW["allowlist"][0]])
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(edited))
    router = ModelRouter(path)
    dropped = RAW["allowlist"][1]
    with pytest.raises(ModelRoutingError):
        router.validate_model_id(dropped)
    with pytest.raises(ModelRoutingError):
        router.calculate_cost(dropped, ModelCallUsage(1, 1))


def test_cost_uses_configured_prices() -> None:
    router = ModelRouter()
    model = RAW["allowlist"][0]
    price = RAW["models"][model]["pricing"]
    cost = router.calculate_cost(model, ModelCallUsage(1_000_000, 1_000_000))
    expected = Decimal(str(price["input_usd_per_million"])) + Decimal(
        str(price["output_usd_per_million"])
    )
    assert cost == expected


def test_quality_checks_hold_on_a_denylisted_term_but_not_on_plain_english() -> None:
    router = ModelRouter()
    tenant, run = uuid4(), uuid4()
    base = {"cited_source_codes": ["S-0001"], "deliverables": ["summary.docx"]}
    sources = [{"source_code": "S-0001"}]
    clean = router.execute_quality_checks(
        tenant, run, {**base, "report_content": "A parallel effort in hiring."}, sources
    )
    assert clean.passed
    vendor = next(n for n in vendor_names(load_providers_config()).values() if n != "Parallel")
    leaked = router.execute_quality_checks(
        tenant, run, {**base, "report_content": f"Found via {vendor}, 40 credits."}, sources
    )
    assert leaked.held_for_review and leaked.reasons
    assert all(vendor not in r for r in leaked.reasons)  # the pattern, never the text
