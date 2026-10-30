"""DEV_UNCAPPED lifts breadth and spend caps on the local stack only; RESEARCH_CAPS=off lifts them
in any environment (owner decision 2026-10-21)."""

from __future__ import annotations

import pytest

from account_intelligence.dev_limits import UNCAPPED, dev_uncapped, research_uncapped


def test_only_the_local_stack_with_the_switch_is_uncapped() -> None:
    assert dev_uncapped({"APP_ENV": "dev", "DEV_UNCAPPED": "1"})
    assert dev_uncapped({"DEV_UNCAPPED": "1"})  # APP_ENV defaults to dev
    assert not dev_uncapped({"APP_ENV": "dev"})
    assert not dev_uncapped({"APP_ENV": "dev", "DEV_UNCAPPED": "true"})
    for cloud in ("staging", "prod", "production", "test"):
        assert not dev_uncapped({"APP_ENV": cloud, "DEV_UNCAPPED": "1"})


def test_the_switch_lifts_job_people_and_agent_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    from agents.contracts import load_registry
    from jobs.config import load_jobs_config
    from people.config import load_people_config

    capped_people = load_people_config.__wrapped__()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DEV_UNCAPPED", "1")
    jobs = load_jobs_config.__wrapped__()
    assert jobs.caps["max_page_reads"] == jobs.caps["max_extractor_calls"] == UNCAPPED
    people = load_people_config.__wrapped__()
    assert people.technologies_per_lane == UNCAPPED
    assert people.caps["max_people_per_run"] == UNCAPPED
    # windows and guards stay as configured
    assert people.caps["recent_activity_days"] == capped_people.caps["recent_activity_days"]
    assert people.caps["max_gap_passes"] == capped_people.caps["max_gap_passes"]
    assert all(c.budgets.tool_calls == UNCAPPED for c in load_registry().values())


def test_research_caps_off_lifts_the_caps_in_any_environment() -> None:
    for env in ("dev", "staging", "prod"):
        assert research_uncapped({"APP_ENV": env, "RESEARCH_CAPS": "off"})
        assert research_uncapped({"APP_ENV": env, "RESEARCH_CAPS": " OFF "})
        assert not research_uncapped({"APP_ENV": env})
        assert not research_uncapped({"APP_ENV": env, "RESEARCH_CAPS": "on"})
    assert research_uncapped({"DEV_UNCAPPED": "1"})  # the local switch still lifts them
    assert not research_uncapped({"APP_ENV": "staging", "DEV_UNCAPPED": "1"})


def test_caps_off_lifts_job_people_and_agent_caps_in_staging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.contracts import load_registry
    from jobs.config import load_jobs_config
    from people.config import load_people_config

    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.delenv("DEV_UNCAPPED", raising=False)
    monkeypatch.setenv("RESEARCH_CAPS", "off")
    assert load_jobs_config.__wrapped__().caps["max_page_reads"] == UNCAPPED
    assert load_people_config.__wrapped__().caps["max_people_per_run"] == UNCAPPED
    assert all(c.budgets.tool_calls == UNCAPPED for c in load_registry().values())


def test_a_model_agent_has_no_token_ceiling_with_the_caps_off(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Staging 2026-10-30: the per-agent token budget with the caps off was a count-sized
    1,000,000, and a reading step on a large account used more, so 95 groups of facts were
    dropped unread. With the caps off a model agent's token budget is effectively unlimited."""
    from account_intelligence.dev_limits import UNCAPPED_TOKENS
    from agents.contracts import load_registry

    monkeypatch.setenv("RESEARCH_CAPS", "off")
    model_agents = [a for a in load_registry().values() if a.model_tier is not None]
    assert model_agents
    for agent in model_agents:
        assert agent.budgets.tokens == UNCAPPED_TOKENS >= 10**11
    monkeypatch.delenv("RESEARCH_CAPS")
    assert all(a.budgets.tokens < 10**9 for a in load_registry().values())
