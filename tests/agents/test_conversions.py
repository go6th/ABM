"""SaaS conversions (guide S15 to S21): each conversion has its test, named in config."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agents.config import load_skill_config
from agents.ports import TenantAgentSettings
from tests.agents.conftest import Harness

CFG = load_skill_config()


def test_every_conversion_names_an_existing_test_and_enforcer() -> None:
    import importlib

    defined = set(re.findall(r"def (test_\w+)", Path(__file__).read_text()))
    for c in CFG.conversions:
        assert c.test in defined, c.id
        module, _, attr = c.enforced_by.rpartition(".")
        assert hasattr(importlib.import_module(module), attr), c.enforced_by
    assert {c.kind for c in CFG.conversions} <= {
        "setting",
        "approval",
        "setting_and_approval",
        "off_by_default",
        "off_for_clients",
    }


def test_conversion_trigger_is_a_typed_request() -> None:
    from agents.schemas import RunRequest

    with pytest.raises(ValueError):
        RunRequest.model_validate({"text": "research Acme for me"})
    with pytest.raises(ValueError):
        RunRequest.model_validate(
            {"account_name": "Acme", "domain": "acme.example", "tenant_id": "x"}
        )


@pytest.mark.asyncio
async def test_conversion_cap_confirmation_is_setting_plus_approval(h: Harness) -> None:
    h.settings.settings = TenantAgentSettings(pursuing_client=None, lanes=("cobol",))
    _, state = await h.run("a")
    assert state["error_code"] == "invalid_setup"  # lanes must be skill lanes
    h.settings.settings = TenantAgentSettings(pursuing_client="Partner", lanes=("salesforce",))
    record = await h.start("b", attended=True, budget=None)
    await h.service.execute(h.tenant, record.run_id)
    assert (await h.service.pending(h.tenant, record.run_id))[0]["awaiting"] == "approval"


@pytest.mark.asyncio
async def test_conversion_ask_before_spend_is_the_confirmed_credits(h: Harness) -> None:
    """The skill's "ask before spending" is answered by the credits confirmed before the run
    (REPLICA_PROMPT): reaching them stops paid work, it never pauses to ask again."""
    record = await h.start("r", attended=True, budget=60_000)
    state = await h.service.execute(h.tenant, record.run_id)
    assert not await h.service.pending(h.tenant, record.run_id)
    assert state["counters"].get("budget_exhausted") == 1


@pytest.mark.asyncio
async def test_conversion_sense_check_pulls_need_approval(make) -> None:  # type: ignore[no-untyped-def]
    from tests.agents import worlds

    h = make(worlds.tangled())
    pull, _ = await h.run("p", mode="pull")
    record = await h.start("b", mode="build", parent_run_id=pull.run_id, attended=True, budget=0)
    await h.service.execute(h.tenant, record.run_id)
    assert (await h.service.pending(h.tenant, record.run_id))[0]["reason"] == "sense_pulls"
    assert h.credit.requests and h.credit.requests[0][1] == 1


@pytest.mark.asyncio
async def test_conversion_consent_flow_is_a_tenant_record(h: Harness) -> None:
    record, _ = await h.run("a")
    assert any(
        r.blocked_reason == "consent_required" for r in h.manifests.by_run[record.run_id].rows
    )
    h.grant_consent()
    record, _ = await h.run("b")
    assert not any(
        r.blocked_reason == "consent_required" for r in h.manifests.by_run[record.run_id].rows
    )
    assert CFG.consent_label == "People data consent"


def test_conversion_internal_history_off_by_default() -> None:
    from agents.guards import internal_history_allowed
    from agents.ports import InternalHistorySettings

    assert CFG.internal_history_default is False
    assert TenantAgentSettings(pursuing_client="x", lanes=()).internal_history.enabled is False
    assert not internal_history_allowed(InternalHistorySettings(), CFG.internal_history_default)


def test_conversion_operator_only_file_is_internal_only() -> None:
    import subprocess
    import sys

    import yaml

    spec = yaml.safe_load(Path("config/output_spec.yaml").read_text())
    names = {d["id"]: d["filename"] for d in spec["deliverables"]}
    assert names["operator_only"] == "{account}_Internal_Only"
    result = subprocess.run(
        [sys.executable, "scripts/abm_name_scan.py"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout


def test_conversion_updates_go_through_the_repository() -> None:
    assert CFG.update_channel == "repository"
    src = " ".join(p.read_text() for p in Path("src/agents").rglob("*.py"))
    assert "repackage" not in src and "skill_creator" not in src
