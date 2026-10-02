from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.agents.model_routing import load_model_routes
from src.agents.people_sources import build_people_source_router
from src.benchmarks.golden_accounts import load_golden_accounts
from src.benchmarks.model_split import load_real_benchmark, summarize_arm
from src.db.data_layer import DataLayerError

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_GOLDENS = ROOT / "tests" / "fixtures" / "synthetic" / "golden_accounts.json"
PEOPLE_CONFIG = ROOT / "config" / "people_sources.yaml"


def test_model_role_routes_are_configured_by_tier_and_environment() -> None:
    proposed = load_model_routes(environment={})
    assert proposed["planner"].tier == "fast"
    assert proposed["extractor"].tier == "fast"
    assert proposed["judge"].tier == "strong"
    assert proposed["planner"].model_id is None
    configured = load_model_routes(
        environment={
            "MODEL_FAST_PROVIDER": "recorded-fixture",
            "MODEL_FAST_ID": "synthetic-model-id",
            "MODEL_STRONG_PROVIDER": "recorded-fixture",
            "MODEL_STRONG_ID": "synthetic-judge-id",
        }
    )
    assert configured["planner"].model_id == "synthetic-model-id"
    with pytest.raises(DataLayerError, match="both provider and model"):
        load_model_routes(environment={"MODEL_FAST_ID": "incomplete"})


def _synthetic_arm_metrics(
    coverage: tuple[int, int], citation: tuple[int, int], cost: float, seconds: float
) -> dict[str, Any]:
    return {
        "coverage": {"numerator": coverage[0], "denominator": coverage[1]},
        "citation_validity": {"numerator": citation[0], "denominator": citation[1]},
        "cost_usd": cost,
        "elapsed_seconds": seconds,
    }


def test_model_benchmark_computes_metrics_without_accepting_synthetic_as_results(
    tmp_path: Path,
) -> None:
    synthetic_records = [
        {
            "account_id": f"synthetic-{index}",
            "arms": {
                "single": _synthetic_arm_metrics((8, 10), (9, 10), 2.0, 40.0),
                "split": _synthetic_arm_metrics((9, 10), (10, 10), 1.0, 25.0),
            },
        }
        for index in range(3)
    ]
    result = summarize_arm(synthetic_records, "split")
    assert result.coverage == 0.9
    assert result.citation_validity == 1.0
    assert result.cost_usd == 1.0
    assert result.elapsed_seconds_mean == 25.0

    synthetic_path = tmp_path / "synthetic_model_benchmark.json"
    synthetic_path.write_text(
        json.dumps({"dataset_type": "synthetic_fixture", "accounts": synthetic_records}),
        encoding="utf-8",
    )
    with pytest.raises(DataLayerError, match="real_september_accounts"):
        load_real_benchmark(synthetic_path)


def test_model_benchmark_rejects_incomplete_or_invalid_metrics() -> None:
    records = [
        {
            "account_id": f"synthetic-{index}",
            "arms": {
                "single": _synthetic_arm_metrics((8, 10), (9, 10), 2.0, 40.0),
                "split": _synthetic_arm_metrics((9, 10), (10, 10), 1.0, 25.0),
            },
        }
        for index in range(3)
    ]
    with pytest.raises(DataLayerError, match="exactly three accounts"):
        summarize_arm(records[:2], "single")
    with pytest.raises(DataLayerError, match="exactly three accounts"):
        summarize_arm(records, "unknown")

    invalid_ratio = copy.deepcopy(records)
    invalid_ratio[0]["arms"]["single"]["coverage"]["denominator"] = 0
    with pytest.raises(DataLayerError, match="0 <= numerator"):
        summarize_arm(invalid_ratio, "single")

    invalid_cost = copy.deepcopy(records)
    invalid_cost[0]["arms"]["single"]["cost_usd"] = True
    with pytest.raises(DataLayerError, match="non-negative number"):
        summarize_arm(invalid_cost, "single")


def test_golden_loader_rejects_malformed_synthetic_inputs(tmp_path: Path) -> None:
    base = json.loads(SYNTHETIC_GOLDENS.read_text(encoding="utf-8"))
    malformed: list[tuple[object, str]] = [
        ([], "JSON object"),
        ({"dataset_type": "other", "accounts": []}, "must be analyst_reviewed_real"),
        ({**base, "accounts": base["accounts"][:2]}, "three to five accounts"),
    ]

    duplicate = copy.deepcopy(base)
    duplicate["accounts"][1]["account_id"] = duplicate["accounts"][0]["account_id"]
    malformed.append((duplicate, "duplicate golden account ID"))

    invalid_capture_date = copy.deepcopy(base)
    invalid_capture_date["accounts"][0]["capture_date"] = "not-a-date"
    malformed.append((invalid_capture_date, "ISO capture_date"))

    missing_review = copy.deepcopy(base)
    missing_review["accounts"][0].pop("analyst_review")
    malformed.append((missing_review, "analyst_review metadata"))

    missing_facts = copy.deepcopy(base)
    missing_facts["accounts"][0]["known_correct_facts"] = []
    malformed.append((missing_facts, "known-correct facts"))

    invalid_grade = copy.deepcopy(base)
    invalid_grade["accounts"][0]["known_correct_facts"][0]["grade"] = "synthetic-grade"
    malformed.append((invalid_grade, "invalid product grade"))

    missing_value = copy.deepcopy(base)
    missing_value["accounts"][0]["known_correct_facts"][0].pop("value")
    malformed.append((missing_value, "requires its known value"))

    invalid_fact_capture = copy.deepcopy(base)
    invalid_fact_capture["accounts"][0]["known_correct_facts"][0]["captured_at"] = "bad-date"
    malformed.append((invalid_fact_capture, "requires captured_at"))

    for index, (document, message) in enumerate(malformed):
        path = tmp_path / f"synthetic_invalid_golden_{index}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(DataLayerError, match=message):
            load_golden_accounts(path, allow_synthetic=True)


def test_golden_loader_requires_explicit_synthetic_opt_in_and_loads_three() -> None:
    with pytest.raises(DataLayerError, match="allow_synthetic"):
        load_golden_accounts(SYNTHETIC_GOLDENS)
    golden_set = load_golden_accounts(SYNTHETIC_GOLDENS, allow_synthetic=True)
    assert golden_set.dataset_type == "synthetic_fixture"
    assert len(golden_set.accounts) == 3
    assert all(account.account_id.startswith("synthetic-") for account in golden_set.accounts)


class _RecordedPeopleAdapter:
    def __init__(self, name: str) -> None:
        self.name = name

    async def find_people(self, query: str) -> list[dict[str, str]]:
        return [{"provider": self.name, "query": query}]

    async def find_organizations(self, query: str) -> list[dict[str, str]]:
        return [{"provider": self.name, "query": query}]

    async def find_tech_stack(self, query: str) -> list[dict[str, str]]:
        return [{"provider": self.name, "query": query}]

    async def find_teams(self, query: str) -> list[dict[str, str]]:
        return [{"provider": self.name, "query": query}]


def test_people_provider_switch_keeps_sumble_for_org_tech_and_teams(tmp_path: Path) -> None:
    config = yaml.safe_load(PEOPLE_CONFIG.read_text(encoding="utf-8"))
    bitscale, sumble = _RecordedPeopleAdapter("bitscale"), _RecordedPeopleAdapter("sumble")
    router = build_people_source_router(bitscale, sumble)
    assert len(router.people_adapters) == 1

    config["mode"] = "bitscale_plus_sumble"
    alternate_path = tmp_path / "people_sources.yaml"
    alternate_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    combined = build_people_source_router(bitscale, sumble, config_path=alternate_path)
    assert len(combined.people_adapters) == 2
    assert [row["provider"] for row in asyncio.run(combined.find_people("synthetic query"))] == [
        "bitscale",
        "sumble",
    ]
    for operation in ("find_organizations", "find_tech_stack", "find_teams"):
        records = asyncio.run(getattr(combined, operation)("synthetic query"))
        assert records[0]["provider"] == "sumble"
