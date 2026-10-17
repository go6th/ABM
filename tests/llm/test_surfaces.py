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
from llm.choice import catalogue

# model ids come from the catalogue, never as literals (the R1 static guard scans for them)
OPUS, FABLE, KIMI = catalogue().ids


def test_the_shipped_block_is_the_owner_decision() -> None:
    surfaces = load_surfaces()
    assert chat_effort() == "high"  # Ask and follow-up chat providers are built at effort high
    assert surfaces["chat"] == SurfaceTarget("chat", None, "high")
    assert surfaces["eval"] == SurfaceTarget("eval", KIMI, "high")


def test_an_absent_block_reproduces_the_prior_behavior(tmp_path) -> None:
    path = tmp_path / "models.yaml"
    path.write_text("version: test\n")  # no surfaces key
    assert load_surfaces(path) == {}
    # the eval fallback keeps the landed suite: kimi-k3 at effort high
    assert resolve_eval_choice({}) == SurfaceTarget("eval", KIMI, "high")


def test_eval_choice_env_overrides() -> None:
    target = resolve_eval_choice({"EVAL_MODEL": FABLE, "EVAL_EFFORT": "low"})
    assert target == SurfaceTarget("eval", FABLE, "low")
    target = resolve_eval_choice({"EVAL_MODEL": OPUS})
    assert target.model == OPUS and target.effort == "high"


def test_eval_choice_rejects_a_model_not_in_the_choices() -> None:
    with pytest.raises(ValueError, match="not a choices model"):
        resolve_eval_choice({"EVAL_MODEL": "some-other-model"})
    with pytest.raises(ValueError, match="unknown effort"):
        resolve_eval_choice({"EVAL_EFFORT": "ludicrous"})


def test_a_surface_must_name_a_choices_model_and_a_known_effort(tmp_path) -> None:
    path = tmp_path / "models.yaml"
    path.write_text("surfaces:\n  eval: {model: some-other-model, effort: high}\n")
    with pytest.raises(ValueError, match="not a choices model"):
        load_surfaces(path)
    path.write_text("surfaces:\n  chat: {model: null, effort: ludicrous}\n")
    with pytest.raises(ValueError, match="unknown effort"):
        load_surfaces(path)
