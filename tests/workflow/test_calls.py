"""Planned calls: validation before spend and the retry-ownership decision per attempt (W6).

Replaces the retired starter graph's ``blocked_or_retry`` test on the one run path.
GUARANTEE(test_retryable_errors_are_reraised_below_the_budget_and_blocked_at_it)
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.connectors import service
from account_intelligence.connectors.base import Operation
from account_intelligence.connectors.errors import (
    BudgetExceeded,
    CircuitOpen,
    CreditsExhausted,
    NonRetryableProviderError,
    NotSupportedYet,
    RetryableProviderError,
    SchemaDriftError,
)
from account_intelligence.connectors.models import WebSearchArgs
from account_intelligence.workflow import calls as calls_mod
from account_intelligence.workflow.calls import execute, operations_for, validate_plan

OPS = operations_for(["web_search", "news_feed"])


def test_plan_validation_refuses_everything_not_allowed() -> None:
    plan = {
        "calls": [
            {"row": "company_news", "operation": "web_search", "args": {"queries": ["acme news"]}},
            {"row": "company_news", "operation": "people_by_title", "args": {"titles": ["cto"]}},
            {"row": "not_owned", "operation": "web_search", "args": {"queries": ["x"]}},
            {
                "row": "company_news",
                "operation": "news_events",
                "args": {"domain": "other.example"},
            },
            {
                "row": "company_news",
                "operation": "web_search",
                "args": {"queries": ["x"], "tenant_id": "t"},
            },
            {"row": "company_news", "operation": "web_search", "args": {"queries": ["second"]}},
            "garbage",
        ]
    }
    calls, refused = validate_plan(plan, ["company_news"], OPS, "acme.example", limit=1)
    assert [c[1] for c in calls] == [Operation.WEB_SEARCH] and refused == 6
    assert validate_plan(None, ["x"], OPS, "acme.example", 3) == ([], 0)  # type: ignore[arg-type]


class _Deps:
    connectors = object()
    retry_max_attempts = 3

    def call_context(self) -> Any:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "reraised", "cited"),
    [
        (RetryableProviderError(attempt=1), True, False),
        (RetryableProviderError(attempt=2), True, False),
        (RetryableProviderError(attempt=3), False, True),
        (CreditsExhausted(), False, True),
        (CircuitOpen(), False, True),
        (SchemaDriftError(), False, True),
        (NonRetryableProviderError(), False, True),
    ],
)
async def test_retryable_errors_are_reraised_below_the_budget_and_blocked_at_it(
    monkeypatch: pytest.MonkeyPatch, error: Exception, reraised: bool, cited: bool
) -> None:
    error.call_ref = "ref-" + uuid4().hex  # type: ignore[attr-defined]

    async def failing(*args: Any, **kw: Any) -> Any:
        raise error

    monkeypatch.setattr(calls_mod, "call_operation", failing)
    plan = [("company_news", Operation.WEB_SEARCH, WebSearchArgs(queries=("q",)))]
    if reraised:
        with pytest.raises(RetryableProviderError):
            await execute(_Deps(), plan, ["company_news"])  # type: ignore[arg-type]
        return
    batch = await execute(_Deps(), plan, ["company_news"])  # type: ignore[arg-type]
    assert (batch.rows["company_news"].refs == [error.call_ref]) is cited  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_budget_stops_the_batch_and_unsupported_is_not_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def budget(*args: Any, **kw: Any) -> Any:
        raise BudgetExceeded()

    monkeypatch.setattr(calls_mod, "call_operation", budget)
    plan = [("a", Operation.WEB_SEARCH, WebSearchArgs(queries=("q",)))] * 3
    batch = await execute(_Deps(), plan, ["a"])  # type: ignore[arg-type]
    assert batch.budget_exhausted and batch.rows["a"].unattempted == "budget_cap"

    async def unsupported(*args: Any, **kw: Any) -> Any:
        raise NotSupportedYet()

    monkeypatch.setattr(calls_mod, "call_operation", unsupported)
    batch = await execute(_Deps(), plan[:1], ["a"])  # type: ignore[arg-type]
    assert batch.rows["a"].unattempted == "not_available_yet" and batch.rows["a"].refs == []
    assert service  # the real call_operation is what production uses
