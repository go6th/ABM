"""Registry contracts (D-95): allow-lists, schemas, budgets, guards; the pull layer has no model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from agents.config import load_skill_config
from agents.contracts import KNOWN_TOOLS, BudgetMeter, ToolBox, load_registry
from agents.errors import AgentConfigInvalid, BudgetExceeded, ToolNotAllowed
from agents.guards import routing_violations
from agents.manifest import ManifestRow
from agents.schemas import SCHEMAS, SenseKind
from llm.config import load_routing_config

REG = load_registry()
T0 = datetime(2026, 10, 8, tzinfo=UTC)


def test_every_agent_has_known_tools_schemas_budgets_and_guards() -> None:
    assert set(REG) == {"setup", "pull", "pass1", "pass2", "people", "sense_check", "distill"}
    for c in REG.values():
        assert c.tools <= KNOWN_TOOLS and c.guards and c.budgets.tool_calls > 0
        assert c.input_schema.__name__ in SCHEMAS or c.input_schema in SCHEMAS.values()


def write(tmp_path: Path, agents: dict) -> Path:  # type: ignore[type-arg]
    p = tmp_path / "agents.yaml"
    p.write_text(yaml.safe_dump({"agents": agents}))
    return p


BASE = {
    "purpose": "x",
    "step": 2,
    "model_tier": "extractor",
    "task_kind": "abm_pass2_extract",
    "tools": ["payloads.read"],
    "input_schema": "ExtractionInput",
    "output_schema": "ExtractionOutput",
    "budgets": {"tokens": 10, "tool_calls": 1, "seconds": 1},
    "guards": ["G4"],
}


@pytest.mark.parametrize(
    "change",
    [
        {"tools": ["shell.run"]},
        {"tools": ["http.get"]},
        {"tools": ["sql.execute"]},
        {"guards": []},
        {"guards": ["G99"]},
        {"output_schema": "FreeText"},
        {"model_tier": None},
        {"step": 7},
        {"budgets": {"tokens": 0, "tool_calls": 1, "seconds": 1}},
        {"budgets": {"tokens": 1, "tool_calls": 0, "seconds": 1}},
    ],
)
def test_registry_rejects_unknown_tools_schemas_and_missing_guards(tmp_path, change) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AgentConfigInvalid):
        load_registry(write(tmp_path, {"bad": {**BASE, **change}}))


def test_registry_rejects_empty_and_bad_code_only_steps(tmp_path: Path) -> None:
    with pytest.raises(AgentConfigInvalid):
        load_registry(write(tmp_path, {}))
    p = tmp_path / "a.yaml"
    p.write_text(yaml.safe_dump({"agents": {"ok": BASE}, "code_only_steps": [{"step": 3}]}))
    with pytest.raises(AgentConfigInvalid):
        load_registry(p)


def test_no_model_is_bound_to_build_gate_or_delivery() -> None:
    doc = yaml.safe_load(Path("config/agents.yaml").read_text())
    assert {s["step"] for s in doc["code_only_steps"]} == {6, 7, 8}
    assert all(c.step not in (6, 7, 8) for c in REG.values() if c.model_tier)


def test_g5_pull_layer_has_no_model_and_no_interpretive_output() -> None:
    pull = REG["pull"]
    assert pull.model_tier is None and pull.task_kind is None
    assert pull.output_schema is ManifestRow
    fields = set(ManifestRow.model_fields)
    assert fields == {
        "row_key",
        "status",
        "payload_refs",
        "call_refs",
        "queries",
        "cost_micros",
        "pulled_at",
        "blocked_reason",
    }
    interpretive = {"summary", "finding", "interpretation", "notes", "value", "grade", "text"}
    assert not fields & interpretive
    with pytest.raises(ValueError):
        ManifestRow.model_validate(
            {
                "row_key": "a",
                "status": "stored",
                "payload_refs": ["x"],
                "pulled_at": T0,
                "summary": "Acme is growing",
            }
        )
    import agents.pull.agent as pull_module

    source = Path(pull_module.__file__).read_text()
    assert "call_agent" not in source and "AgentModel" not in source


def test_output_schemas_have_no_grade_or_tool_fields() -> None:
    def names(model: type) -> set[str]:
        out = set()
        for name, f in model.model_fields.items():  # type: ignore[attr-defined]
            out.add(name)
            for arg in getattr(f.annotation, "__args__", ()):
                inner = getattr(arg, "__args__", (arg,))
                for t in inner:
                    if hasattr(t, "model_fields"):
                        out |= names(t)
        return out

    for c in REG.values():
        fields = names(c.output_schema)
        assert not fields & {"grade", "confidence", "tool_calls", "tenant_id", "url", "mode"}, c


def test_sense_kinds_match_config() -> None:
    assert set(SenseKind.__args__) == set(load_skill_config().sense_kinds)  # type: ignore[attr-defined]


def test_g11_routes_follow_split_mode() -> None:
    routing = load_routing_config()
    tiers = {k: v.tier for k, v in routing.task_kinds.items()}
    agent_tasks = {name: c.task_kind for name, c in REG.items()}
    assert routing_violations(agent_tasks, tiers, "extractor", "planner") == ()
    assert all(tiers[c.task_kind] == c.model_tier for c in REG.values() if c.task_kind)


def test_g11_verifier_family_independence_is_reported_not_hidden() -> None:
    routing = load_routing_config()
    judge = {routing.candidates[n].family for n in routing.tiers[routing.judge_tier].ladder}
    generators = {
        routing.candidates[n].family
        for t in ("extractor", "planner")
        for n in routing.tiers[t].ladder
    }
    # One model family is configured today: verification is "not independent" and Confirmed is
    # capped by the existing G12 path (docs/routing/DECISIONS.md). This test pins the fact.
    assert judge == generators == {"claude"}


def test_toolbox_refuses_tools_outside_the_allow_list() -> None:
    import asyncio

    async def read(ref: str) -> str:
        return ref

    meter = BudgetMeter(REG["pass2"], T0)
    box = ToolBox(REG["pass2"], {"payloads.read": read}, meter)
    assert asyncio.run(box.call("payloads.read", "pl/1")) == "pl/1"
    with pytest.raises(ToolNotAllowed):
        asyncio.run(box.call("ledger.call_operation", "x"))
    with pytest.raises(ToolNotAllowed):
        ToolBox(REG["pass2"], {"payloads.read": read, "payloads.put": read}, meter)


def test_budget_breach_is_a_typed_end() -> None:
    meter = BudgetMeter(REG["sense_check"], T0)
    for _ in range(REG["sense_check"].budgets.tool_calls):
        meter.charge_call()
    with pytest.raises(BudgetExceeded):
        meter.charge_call()
    assert meter.exit_reason == "budget_tool_calls"
    tokens = BudgetMeter(REG["distill"], T0)
    with pytest.raises(BudgetExceeded):
        tokens.charge_tokens(REG["distill"].budgets.tokens + 1)
    clock = BudgetMeter(REG["distill"], T0)
    clock.check_time(T0 + timedelta(seconds=REG["distill"].budgets.seconds))
    with pytest.raises(BudgetExceeded):
        clock.check_time(T0 + timedelta(seconds=REG["distill"].budgets.seconds + 1))
