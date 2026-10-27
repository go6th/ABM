"""The feed windows come from config/skill/scope.yaml (news 18, postings 12, detections 24
months), counted back from the run's month anchor."""

from __future__ import annotations

from datetime import date

import pytest

from jobs.window import months_before, window_start


def test_months_before_clamps_to_the_month_end_and_crosses_years() -> None:
    assert months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert months_before(date(2026, 10, 1), 18) == date(2025, 4, 1)
    assert months_before(date(2026, 10, 1), 24) == date(2024, 10, 1)
    assert window_start(date(2026, 10, 24)) == months_before(date(2026, 10, 24), 12)
    with pytest.raises(ValueError):
        months_before(date(2026, 1, 1), -1)


def test_each_feed_takes_its_own_window_from_the_scope() -> None:
    from account_intelligence.connectors.base import Operation
    from agents.config import load_skill_config
    from agents.production.provider import feed_since

    scope = load_skill_config().scope
    anchor = date(2026, 10, 1)
    assert feed_since(Operation.JOB_OPENINGS, anchor, scope) == date(2025, 10, 1)
    assert feed_since(Operation.NEWS_EVENTS, anchor, scope) == date(2025, 4, 1)
    assert feed_since(Operation.TECH_DETECTIONS, anchor, scope) == date(2024, 10, 1)
    assert feed_since(Operation.WEB_SEARCH, anchor, scope) is None
