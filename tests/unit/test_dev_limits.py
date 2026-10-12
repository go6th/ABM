"""DEV_UNCAPPED lifts breadth and spend caps on the local stack only."""

from __future__ import annotations

import pytest

from account_intelligence.dev_limits import UNCAPPED, dev_uncapped


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
