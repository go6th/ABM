"""The data-class guard (rule R8, ADR-0133): off changes nothing, warn logs and calls, enforce
refuses before any paid call, and the research graph ends the run partial on a refusal."""

from __future__ import annotations

import logging
from dataclasses import replace
from uuid import uuid4

import pytest

from account_intelligence.platform.hermetic import MemoryPayloadStore, ScriptedLlm
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.spec import load_spec
from llm import data_class as DC


def _deps(llm: ScriptedLlm, **run_input: object) -> RunDeps:
    return RunDeps(
        uuid4(),
        uuid4(),
        uuid4(),
        "acme.example",
        None,  # type: ignore[arg-type]
        llm,
        MemoryPayloadStore(),
        None,  # type: ignore[arg-type]
        load_spec(),
        run_input=dict(run_input),
    )


def _policy(monkeypatch: pytest.MonkeyPatch, mode: str, **allowed: list[str]) -> None:
    policy = DC.DataClassPolicy(mode, {k: frozenset(v) for k, v in allowed.items()})
    monkeypatch.setattr(DC, "load_policy", lambda path=None: policy)


def test_the_shipped_config_is_off_and_lists_every_model() -> None:
    import yaml

    from account_intelligence.config import CONFIG_DIR

    doc = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())
    policy = DC.parse(doc)
    assert policy.mode == "off"
    assert set(policy.allowed) == set(doc["models"])  # every model states what it may receive
    with pytest.raises(ValueError, match="quoted"):
        DC.parse({"data_class_guard": False})  # an unquoted off
    with pytest.raises(ValueError, match="unknown data classes"):
        DC.parse({"models": {"m": {"data_classes_allowed": ["secret"]}}})


def test_check_by_mode() -> None:
    strict = DC.DataClassPolicy("enforce", {"zdr": frozenset({"public_web", "tenant"})})
    assert DC.check(strict, "zdr", "tenant") == "ok"
    assert DC.check(strict, "other", "public_web") == "ok"  # public web goes anywhere
    assert DC.check(strict, "other", "tenant") == "refuse"
    assert DC.check(replace(strict, mode="warn"), "other", "private") == "warn"
    assert DC.check(replace(strict, mode="off"), "other", "private") == "ok"
    with pytest.raises(ValueError):
        DC.check(strict, "zdr", "nonsense")


def test_a_file_run_sends_tenant_data_and_a_company_run_public_web() -> None:
    llm = ScriptedLlm(lambda *a: "{}")
    assert _deps(llm, input_mode="file").data_class == "tenant"
    assert _deps(llm).data_class == "public_web"


@pytest.mark.asyncio
async def test_the_guard_is_off_by_default_and_changes_nothing() -> None:
    DC.load_policy.cache_clear()
    llm = ScriptedLlm(lambda *a: '{"ok": true}')
    deps = _deps(llm, input_mode="file")  # tenant data, no model allows it
    out = await deps.model("abm_pass2_extract", "sys", "payload")
    assert out.text == '{"ok": true}' and len(llm.calls) == 1


@pytest.mark.asyncio
async def test_warn_logs_a_catalogued_event_and_makes_the_call(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _policy(monkeypatch, "warn")
    llm = ScriptedLlm(lambda *a: "{}")
    deps = _deps(llm, input_mode="file")
    with caplog.at_level(logging.WARNING):
        await deps.model("abm_pass2_extract", "sys", "payload")
    assert len(llm.calls) == 1
    events = [r for r in caplog.records if r.getMessage() == "model.data_class_violation"]
    assert events and events[0].__dict__["outcome"] == "warned"
    assert events[0].__dict__["reason"] == "tenant"


@pytest.mark.asyncio
async def test_enforce_refuses_before_any_paid_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _policy(monkeypatch, "enforce")
    llm = ScriptedLlm(lambda *a: "{}")
    deps = _deps(llm, input_mode="file")
    with pytest.raises(DC.DataClassRefused):
        await deps.model("abm_pass2_extract", "sys", "payload")
    assert llm.calls == []  # nothing was sent
    # public web data, and a model that lists the class, still go through
    await _deps(llm).model("abm_pass2_extract", "sys", "payload")
    assert len(llm.calls) == 1


def test_the_research_graph_ends_a_refused_run_partial_with_a_reason() -> None:
    from account_intelligence.replica.workspace import STOP_TEXT
    from agents.graph import model_failure_code

    code = model_failure_code(DC.DataClassRefused("m", "tenant"))
    assert code == "model_data_class_refused" and code in STOP_TEXT
