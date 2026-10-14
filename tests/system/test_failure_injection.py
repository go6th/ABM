"""Failure injection on the whole run (integration spec 9): provider outage, schema drift and the
budget cap. A failed source makes its rows Blocked with a reason and the run continues; the run
is never FAILED for a provider failure; the budget cap ends BUDGET_EXCEEDED after distill ran.
GUARANTEE(test_outage_gives_blocked_rows_and_a_partial_run)
GUARANTEE(test_budget_cap_ends_budget_exceeded_with_distill_run)
"""

from __future__ import annotations

import pytest

from account_intelligence.connectors.base import ProviderCode
from tests.connectors.conftest import Harness, Reply
from tests.system.world import admin, settled_once, start_run

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def test_outage_gives_blocked_rows_and_a_partial_run(harness: Harness) -> None:
    run = await start_run(harness)
    # the structured feed provider is down for the whole run (after the node retry budget)
    harness.providers[ProviderCode.P_SIGNALS].fallback = Reply(status=503, body={})
    await run.scheduler.drain()
    status = admin("SELECT status FROM account_runs WHERE id = %s", run.run)[0][0]
    assert status == "PARTIAL"  # never FAILED for a provider outage
    rows = dict(admin("SELECT row_key, status FROM coverage_rows WHERE run_id = %s", run.run))
    reasons = dict(
        admin("SELECT row_key, reason_code FROM coverage_rows WHERE run_id = %s", run.run)
    )
    assert rows["job_postings_12_months"] == "blocked"
    assert reasons["job_postings_12_months"] == "source_unavailable"


async def test_budget_cap_ends_budget_exceeded_with_distill_run(harness: Harness) -> None:
    run = await start_run(harness)
    with __import__("psycopg").connect(
        __import__("tests.tracker.world", fromlist=["x"]).admin_dsn(), autocommit=True
    ) as c:
        c.execute("UPDATE account_runs SET tool_call_cap = 2 WHERE id = %s", (run.run,))
    await run.scheduler.drain()
    ((status, reason),) = admin(
        "SELECT status, finished_reason FROM account_runs WHERE id = %s", run.run
    )
    assert (status, reason) == ("BUDGET_EXCEEDED", "budget_cap")
    calls = admin("SELECT tool_calls_used, tool_call_cap FROM account_runs WHERE id = %s", run.run)
    assert calls[0][0] <= calls[0][1]  # the cap is never exceeded
    rows = dict(admin("SELECT row_key, reason_code FROM coverage_rows WHERE run_id = %s", run.run))
    assert "budget_cap" in rows.values()
    assert settled_once(run.run)
    assert admin("SELECT stop_reason FROM research_jobs WHERE run_id = %s", run.run) == [
        ("credit_cap_reached",)
    ]
