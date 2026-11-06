"""The user's model choice: roles by tier, the independent judge, and why a model is greyed out."""

from __future__ import annotations

from typing import Any

import pytest

from llm.choice import NOT_ENABLED, NOT_SET_UP, RunModels, availability, catalogue

# model ids live only in config (R1): the three choices in their configured order
OPUS, FABLE, KIMI, SONNET, HAIKU, GROK = catalogue().ids
KEYS = {"ANTHROPIC_API_KEY": "x", "MOONSHOT_API_KEY": "y"}


def test_the_catalogue_offers_six_models_with_the_owners_defaults() -> None:
    cat = catalogue()
    # every Claude model is a choice since 2026-10-20 (owner decision), plus Kimi K3 and Grok 4.5
    assert len(cat.ids) == 6
    assert (cat.default_research, cat.default_analysis) == (
        OPUS,
        OPUS,
    )  # analysis default Opus 5.5 since 2026-10-20 (token review item 4)
    assert [m.line for m in cat.models] == [
        "Most capable, slower",
        "Fast and strong",
        "Kimi K3 by Moonshot AI",
        "Fast and economical",
        "Fastest and cheapest, for simple work",
        "Grok 4.5 by xAI",
    ]
    # the effort beside each model: medium or high, medium by default for both roles
    assert cat.efforts == ("medium", "high")
    assert (cat.default_research_effort, cat.default_analysis_effort) == ("medium", "medium")
    kimi = cat.get(KIMI)
    assert kimi is not None and kimi.provider == "moonshot"
    assert "web_search" not in kimi.features and "citations" not in kimi.features
    # Grok 4.5 (owner decision 2026-10-20): its own provider and key, allowed for every client
    grok = cat.get(GROK)
    assert grok is not None and (grok.provider, grok.key_env) == ("xai", "XAI_API_KEY")
    assert grok.features == ("strict_json",) and GROK not in cat.needs_client_permission


def test_tiers_follow_the_two_roles() -> None:
    models = RunModels(KIMI, FABLE)
    assert {t: models.for_tier(t) for t in ("planner", "extractor", "classifier")} == {
        "planner": KIMI,
        "extractor": KIMI,
        "classifier": KIMI,
    }
    assert {t: models.for_tier(t) for t in ("synthesis", "narrative", "judge")} == {
        "synthesis": FABLE,
        "narrative": FABLE,
        "judge": FABLE,
    }


def test_the_judge_never_equals_the_research_model() -> None:
    for model in (OPUS, FABLE, KIMI):
        both = RunModels(model, model)
        assert both.for_tier("judge") != both.for_tier("extractor")
    assert RunModels(KIMI, KIMI).for_tier("judge") in (OPUS, FABLE)


def _one_claude_model() -> Any:
    """The catalogue with one Claude model only: no independent judge is possible."""
    import dataclasses

    cat = catalogue()
    return dataclasses.replace(
        cat, models=tuple(m for m in cat.models if m.provider != "anthropic" or m.id == OPUS)
    )


def test_with_no_other_claude_model_the_judge_is_refused_never_shared() -> None:
    from llm.choice import independent_judge
    from llm.route import JudgeNotIndependent

    assert independent_judge(OPUS) != OPUS
    with pytest.raises(JudgeNotIndependent):
        independent_judge(OPUS, _one_claude_model())


def test_a_route_served_judge_on_the_extractors_model_is_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G12 without a stored choice (a run from before the model choice): when the routes put
    the judge on the extractor's model, the judge runs on another Claude model; it used to run
    on the same model, and grading capped every claim silently."""
    from dataclasses import replace
    from uuid import uuid4

    import llm.choice as choice
    from account_intelligence.platform.llm import load_routes
    from account_intelligence.workflow.deps import RunDeps
    from llm.route import JudgeNotIndependent

    routes = dict(load_routes())
    routes["judge"] = replace(routes["judge"], model=routes["extractor"].model)

    class Port:
        def __init__(self) -> None:
            self.routes = routes

        async def complete_as(self, *a: Any, **k: Any) -> Any:
            raise AssertionError("no call")

    deps = RunDeps(
        tenant_id=uuid4(), run_id=uuid4(), account_id=uuid4(), domain="x.example",
        txn=None, llm=Port(), payloads=None, clock=None, spec=None, models=None,
    )  # type: ignore[arg-type]  # fmt: skip
    judge = deps.chosen_model("judge")
    assert judge is not None and judge != routes["extractor"].model
    deps.ensure_independent_judge()  # the run may start
    monkeypatch.setattr(choice, "catalogue", lambda *a: _one_claude_model())
    with pytest.raises(JudgeNotIndependent):
        deps.ensure_independent_judge()


def test_a_model_is_greyed_out_with_the_reason() -> None:
    enabled = [OPUS, FABLE, KIMI]
    assert availability(OPUS, enabled=enabled, allowed=None, env=KEYS) is None
    # every enabled model is allowed by default (owner decision 2026-10-15)
    assert availability(KIMI, enabled=enabled, allowed=None, env=KEYS) is None
    assert availability(KIMI, enabled=enabled, allowed=[OPUS, FABLE, KIMI], env=KEYS) is None
    assert availability(KIMI, enabled=[OPUS, FABLE], allowed=[KIMI], env=KEYS) == NOT_ENABLED
    assert availability(KIMI, enabled=enabled, allowed=[KIMI], env={}) == NOT_SET_UP
    assert availability("unknown", enabled=enabled, allowed=None, env=KEYS) == NOT_ENABLED


def test_the_record_round_trips_with_its_fallbacks() -> None:
    models = RunModels(KIMI, FABLE)
    models.fell_back("abm_pass1_extract", KIMI, OPUS, "schema_invalid")
    again = RunModels.from_json(models.as_json())
    assert again == models
    assert RunModels.from_json(None) == RunModels(OPUS, OPUS)


def test_a_key_loaded_from_the_secret_store_counts_as_set_up(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # the cloud reads the keys from Secret Manager (ANTHROPIC_SECRET, MOONSHOT_SECRET), so no
    # *_API_KEY variable exists; every model was shown "Not set up" until this (2026-10-15)
    from llm import choice

    monkeypatch.setattr(choice, "_LOADED_KEYS", set())
    enabled = [m.id for m in catalogue().models]
    assert availability(OPUS, enabled=enabled, allowed=None, env={}) == NOT_SET_UP
    choice.mark_set_up("ANTHROPIC_API_KEY")
    assert availability(OPUS, enabled=enabled, allowed=None, env={}) is None
    assert availability(KIMI, enabled=enabled, allowed=[KIMI], env={}) == NOT_SET_UP
    choice.mark_set_up("MOONSHOT_API_KEY")
    assert availability(KIMI, enabled=enabled, allowed=[KIMI], env={}) is None
    assert availability(GROK, enabled=enabled, allowed=None, env={}) == NOT_SET_UP
    choice.mark_set_up("XAI_API_KEY")
    assert availability(GROK, enabled=enabled, allowed=None, env={}) is None


def test_grok_is_greyed_out_until_its_key_is_set() -> None:
    """Without XAI_API_KEY the menu shows Grok 4.5 "Not set up"; with it, every client may
    choose it (it is not in needs_client_permission)."""
    enabled = list(catalogue().ids)
    assert availability(GROK, enabled=enabled, allowed=None, env=KEYS) == NOT_SET_UP
    with_key = {**KEYS, "XAI_API_KEY": "z"}
    assert availability(GROK, enabled=enabled, allowed=None, env=with_key) is None
    assert RunModels(GROK, GROK).for_tier("judge") in (OPUS, FABLE)
