"""The ``surfaces`` block of models.yaml: the one model/effort setting every conversational
surface reads (owner decision 2026-10-17 — the research graph, Ask and the evals all run on
Kimi K3, Claude Fable 5.1 or Claude Opus 5.5; switching is config, never code)."""

from __future__ import annotations

import pytest

from account_intelligence.platform.llm import (
    SurfaceTarget,
    chat_effort,
    load_surfaces,
    resolve_eval_choice,
)


def test_the_shipped_block_is_the_owner_decision() -> None:
    surfaces = load_surfaces()
    assert chat_effort() == "high"  # Ask and follow-up chat providers are built at effort high
    assert surfaces["chat"] == SurfaceTarget("chat", None, "high")
    assert surfaces["eval"] == SurfaceTarget("eval", "kimi-k3", "high")


def test_an_absent_block_reproduces_the_prior_behavior(tmp_path) -> None:
    path = tmp_path / "models.yaml"
    path.write_text("version: test\n")  # no surfaces key
    assert load_surfaces(path) == {}
    # the eval fallback keeps the landed suite: kimi-k3 at effort high
    assert resolve_eval_choice({}) == SurfaceTarget("eval", "kimi-k3", "high")


def test_eval_choice_env_overrides() -> None:
    target = resolve_eval_choice({"EVAL_MODEL": "claude-fable-5-1", "EVAL_EFFORT": "low"})
    assert target == SurfaceTarget("eval", "claude-fable-5-1", "low")
    target = resolve_eval_choice({"EVAL_MODEL": "claude-opus-5-5"})
    assert target.model == "claude-opus-5-5" and target.effort == "high"


def test_eval_choice_rejects_a_model_not_in_the_choices() -> None:
    with pytest.raises(ValueError, match="not a choices model"):
        resolve_eval_choice({"EVAL_MODEL": "gpt-9"})
    with pytest.raises(ValueError, match="unknown effort"):
        resolve_eval_choice({"EVAL_EFFORT": "ludicrous"})


def test_a_surface_must_name_a_choices_model_and_a_known_effort(tmp_path) -> None:
    path = tmp_path / "models.yaml"
    path.write_text("surfaces:\n  eval: {model: gpt-9, effort: high}\n")
    with pytest.raises(ValueError, match="not a choices model"):
        load_surfaces(path)
    path.write_text("surfaces:\n  chat: {model: null, effort: ludicrous}\n")
    with pytest.raises(ValueError, match="unknown effort"):
        load_surfaces(path)
