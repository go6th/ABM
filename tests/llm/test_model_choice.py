"""The user's model choice: roles by tier, the independent judge, and why a model is greyed out."""

from __future__ import annotations

from llm.choice import NOT_ENABLED, NOT_SET_UP, RunModels, availability, catalogue

# model ids live only in config (R1): the three choices in their configured order
OPUS, FABLE, KIMI = catalogue().ids
KEYS = {"ANTHROPIC_API_KEY": "x", "MOONSHOT_API_KEY": "y"}


def test_the_catalogue_offers_three_models_with_the_owners_defaults() -> None:
    cat = catalogue()
    assert len(cat.ids) == 3
    assert (cat.default_research, cat.default_analysis) == (OPUS, FABLE)
    assert [m.line for m in cat.models] == [
        "Most capable, slower",
        "Fast and strong",
        "Kimi K3 by Moonshot AI",
    ]
    kimi = cat.get(KIMI)
    assert kimi is not None and kimi.provider == "moonshot"
    assert "web_search" not in kimi.features and "citations" not in kimi.features


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
    assert RunModels.from_json(None) == RunModels(OPUS, FABLE)


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
