"""Credit cap per pull on the whole parallel run path (replica credit model): the credits confirmed
on the client's pool are the run's cap, so a parallel run that reaches them never pauses for an
approval (no AWAITING_APPROVAL, no pause request); it stops paying, keeps what it found, reports
every row and ends BUDGET_EXCEEDED with one settlement, and its job says why.

GUARANTEE(test_a_parallel_run_at_its_credits_stops_cleanly_and_never_pauses)
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from account_intelligence.workflow import parallel_phase
from account_intelligence.workflow.spec import load_spec
from tests.connectors.conftest import Harness
from tests.spend.conftest import admin as admin_exec
from tests.system.world import admin, settled_once, start_run

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def parallel_spec() -> Any:
    spec = load_spec()
    return replace(spec, settings={**spec.settings, "execution_mode": "parallel"})


@pytest.fixture
def tight_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the research phase first starts, shrink the run's budget to what is spent plus one
    cent, so the first paid wave meets the credit limit (test instrumentation only)."""
    original = parallel_phase.research_node
    shrunk: set[UUID] = set()

    def wrapped(handlers: Any, deps: Any, cfg: Any = None) -> Any:
        inner = original(handlers, deps, cfg)

        async def research(state: Any) -> Any:
            if deps.run_id not in shrunk:
                shrunk.add(deps.run_id)
                spent = admin("SELECT spent_usd FROM account_runs WHERE id = %s", deps.run_id)[0][0]
                admin_exec(
                    "UPDATE account_runs SET budget_usd = %s WHERE id = %s",
                    Decimal(spent) + Decimal("0.01"),
                    deps.run_id,
                )
            return await inner(state)

        return research

    monkeypatch.setattr(parallel_phase, "research_node", wrapped)


def status(run: UUID) -> tuple[Any, ...]:
    return admin(
        "SELECT status, awaiting_kind, finished_reason FROM account_runs WHERE id = %s", run
    )[0]


async def test_a_parallel_run_at_its_credits_stops_cleanly_and_never_pauses(
    harness: Harness, tight_cap: None
) -> None:
    run = await start_run(harness, spec=parallel_spec())
    await run.scheduler.drain()
    final, kind, reason = status(run.run)
    assert (final, kind, reason) == ("BUDGET_EXCEEDED", None, "budget_cap")
    assert run.run not in run.scheduler.timeouts  # nobody was asked to approve anything
    assert admin(
        "SELECT count(*) FROM spend_limit_events WHERE run_id = %s AND event = 'pause_opened'",
        run.run,
    ) == [(0,)]
    rows = admin("SELECT status FROM coverage_rows WHERE run_id = %s", run.run)
    assert len(rows) == 33 and all(r[0] is not None for r in rows)  # every row reported
    spent, budget, overshoot = admin(
        "SELECT spent_usd, budget_usd, cap_overshoot_usd FROM account_runs WHERE id = %s", run.run
    )[0]
    assert spent <= budget + overshoot  # it stopped paying at its credits
    assert settled_once(run.run)
    assert admin("SELECT status, stop_reason FROM research_jobs WHERE run_id = %s", run.run) == [
        ("done", "credit_cap_reached")
    ]
