"""Nothing paid starts without a person saying yes twice (``runs.start_guard``, owner's rule
2026-11-05): every research start route refuses a request without both confirmations before any
database work, and the three automatic starters (upload analysis, auto resume, scheduled
refresh) start nothing unless ``RESEARCH_AUTOMATIC_STARTS=on``. SYNTHETIC data only."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.api.errors import APIProblem, require_confirmed_twice
from account_intelligence.runs.start_guard import (
    AUTOMATIC_STARTS_ENV,
    automatic_starts_allowed,
    confirmed_twice,
)

NOW = datetime(2026, 11, 5, tzinfo=UTC)


def _refused(problem: APIProblem) -> bool:
    return problem.status == 428 and problem.type == "confirm_twice"


@pytest.mark.parametrize(
    ("confirm", "again", "ok"),
    [(True, True, True), (True, False, False), (False, True, False), (None, None, False)],
)
def test_a_start_without_both_confirmations_is_refused(
    confirm: bool | None, again: bool | None, ok: bool
) -> None:
    assert confirmed_twice(confirm, again) is ok
    if ok:
        require_confirmed_twice(confirm, again)
        return
    with pytest.raises(APIProblem) as caught:
        require_confirmed_twice(confirm, again)
    assert _refused(caught.value)


def test_automatic_starts_are_off_by_default() -> None:
    assert automatic_starts_allowed({}) is False
    assert automatic_starts_allowed({AUTOMATIC_STARTS_ENV: "off"}) is False
    assert automatic_starts_allowed({AUTOMATIC_STARTS_ENV: "1"}) is False
    assert automatic_starts_allowed({AUTOMATIC_STARTS_ENV: " ON "}) is True


@pytest.mark.asyncio
async def test_every_start_route_refuses_one_confirmation_before_any_work() -> None:
    """The handlers raise before they touch the request, the identity or the database (both are
    None here: any use of them would fail differently)."""
    from account_intelligence.api.routes.credits import RefreshBody, confirm_refresh
    from account_intelligence.api.routes.research import (
        ResearchBody,
        ResumeBody,
        resume_research,
        start_research,
    )
    from account_intelligence.api.routes.runs import RunBody, create_run

    once = {"confirm": True}
    calls: list[Any] = [
        start_research(
            ResearchBody(request_id=uuid4(), company_name="Acme", domain="acme.example", **once),
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
        ),
        resume_research(ResumeBody(run_id=uuid4(), request_id=uuid4(), **once), None, None),  # type: ignore[arg-type]
        confirm_refresh(
            "acme.example",
            RefreshBody(request_id=uuid4(), scope="full", **once),
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
        ),
        create_run(RunBody(domain="acme.example"), None, "k" * 16, None),  # type: ignore[arg-type]
    ]
    for call in calls:
        with pytest.raises(APIProblem) as caught:
            await call
        assert _refused(caught.value)


@pytest.mark.asyncio
async def test_an_upload_starts_no_analysis_while_automatic_starts_are_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from account_intelligence.app import auto_analysis

    monkeypatch.delenv(AUTOMATIC_STARTS_ENV, raising=False)
    # the container is never touched: the guard answers first
    assert await auto_analysis.start(None, uuid4(), uuid4()) is None


@pytest.mark.asyncio
async def test_the_sweep_and_the_schedule_start_nothing_while_automatic_starts_are_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from account_intelligence.replica import auto_resume, schedules

    monkeypatch.delenv(AUTOMATIC_STARTS_ENV, raising=False)

    async def never(*_: Any) -> None:
        raise AssertionError("nothing may start")

    assert await auto_resume.auto_resume_pass(None, NOW) == {"continued": 0, "skipped": 0}
    assert await schedules.tick(NOW, never) == {
        "started": 0,
        "waiting": 0,
        "promoted": 0,
        "deferred": 0,
    }
