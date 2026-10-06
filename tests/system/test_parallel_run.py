"""Parallel execution on the whole run path (real Postgres, real connector runtime over scripted
synthetic providers, real LangGraph checkpointer, scripted LLM): the same rows end in the same
statuses as the sequential order, the research stages overlap in time, every unit event is
recorded, and a finished run's re-delivered slice does nothing.

GUARANTEE(test_parallel_whole_run_completes_with_the_same_rows_as_sequential)
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from account_intelligence.workflow.spec import load_spec
from tests.connectors.conftest import Harness
from tests.system.world import LEDGER_SUM, admin, start_run

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def parallel_spec():  # type: ignore[no-untyped-def]
    spec = load_spec()
    return replace(spec, settings={**spec.settings, "execution_mode": "parallel"})


def rows(run: object) -> dict[str, str]:
    return dict(admin("SELECT row_key, status FROM coverage_rows WHERE run_id = %s", run))


async def test_parallel_whole_run_completes_with_the_same_rows_as_sequential(
    harness: Harness,
) -> None:
    seq = await start_run(harness)
    await seq.scheduler.drain()
    par = await start_run(harness, spec=parallel_spec())
    await par.scheduler.drain()
    ((status, reason),) = admin(
        "SELECT status, finished_reason FROM account_runs WHERE id = %s", par.run
    )
    seq_state = admin("SELECT status, finished_reason FROM account_runs WHERE id = %s", seq.run)
    assert (status, reason) == seq_state[0]
    assert rows(par.run) == rows(seq.run) and len(rows(par.run)) == 33
    ledger = admin(LEDGER_SUM, par.run)
    spent = admin("SELECT spent_usd FROM account_runs WHERE id = %s", par.run)[0][0]
    assert Decimal(ledger[0][0]) == Decimal(spent)
    events = admin(
        "SELECT unit_key, event FROM work_unit_events WHERE run_id = %s ORDER BY id", par.run
    )
    finished = {k for k, e in events if e == "finished"}
    assert finished == {
        f"body|{b}"
        for b in (
            "baseline", "jobs", "name_searches", "news", "pass1_relationship", "people", "tech",
            "verification",
        )
    }
    # the roots started before the first of them finished: they ran concurrently
    first_finish = next(i for i, (_, e) in enumerate(events) if e == "finished")
    started_before = {k for k, e in events[:first_finish] if e == "started"}
    assert len(started_before) >= 2
    entries = dict(admin("SELECT entry, amount FROM wallet_ledger WHERE run_id = %s", par.run))
    assert -entries["reserve"] == entries["commit"] + entries["refund"]  # one settlement


async def test_parallel_rerun_slice_after_finish_is_a_noop(harness: Harness) -> None:
    run = await start_run(harness, spec=parallel_spec())
    await run.scheduler.drain()
    calls = sum(p.calls for p in harness.providers.values())
    n_events = admin("SELECT count(*) FROM work_unit_events WHERE run_id = %s", run.run)
    await run.scheduler.runner(run.tenant, run.run, 99)  # type: ignore[misc]
    assert sum(p.calls for p in harness.providers.values()) == calls
    assert admin("SELECT count(*) FROM work_unit_events WHERE run_id = %s", run.run) == n_events
