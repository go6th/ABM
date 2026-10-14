"""Whole-run system tests (integration spec section 9): the one run path, every arrow.

API-equivalent create_run -> RunScheduler -> worker slice -> 13 stages -> call_operation ->
extractor -> grade_claims -> record_graded_fact -> tracker record_outcome -> gap loop -> build
and gate ports -> finish_run -> single settlement -> delivery note. Real Postgres, real connector
runtime over scripted synthetic providers, real LangGraph checkpointer, scripted LLM.
GUARANTEE(test_golden_run_ends_partial_with_every_row_reported)
GUARANTEE(test_lying_generator_whole_run_persists_no_fabrication)
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from tests.connectors.conftest import Harness
from tests.system.world import LEDGER_SUM, Script, admin, credit_entries, settled_once, start_run

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def test_golden_run_ends_partial_with_every_row_reported(harness: Harness) -> None:
    run = await start_run(harness)
    await run.scheduler.drain()
    ((status, reason),) = admin(
        "SELECT status, finished_reason FROM account_runs WHERE id = %s", run.run
    )
    # no deliverable builder exists yet: never COMPLETED with nothing built
    assert (status, reason) == ("PARTIAL", "builders_not_configured")
    rows = admin("SELECT row_key, status, swept FROM coverage_rows WHERE run_id = %s", run.run)
    assert len(rows) == 33 and all(r[1] is not None for r in rows)
    reported = [r for r in rows if not r[2]]
    assert len(reported) == 33  # every row reported by its stage, none swept
    by = {r[0]: r[1] for r in rows}
    assert by["prior_customer_status"] == "not_done"  # waiting on the account owner
    assert by["company_news"] in ("done", "partial", "none")
    assert by["job_board_by_lane"] != "not_done"  # careers host verified by visiting it
    facts = admin(
        "SELECT count(*) FROM facts WHERE account_id IN"
        " (SELECT account_id FROM account_runs WHERE id = %s)",
        run.run,
    )[0][0]
    assert facts > 0
    ledger = admin(LEDGER_SUM, run.run)
    spent = admin("SELECT spent_usd FROM account_runs WHERE id = %s", run.run)[0][0]
    assert Decimal(ledger[0][0]) == Decimal(spent)  # ledger sum equals the run's spend
    assert settled_once(run.run)  # the run's job settled once on the pool
    assert credit_entries(run.run)["reserve"] == 400
    note_key = [k for k in harness.payloads.objects if k.endswith("delivery/note.json")]
    assert len(note_key) == 1
    note = json.loads(await harness.payloads.get(note_key[0]))
    assert note["deliverables"] == "builders_not_configured"
    blob = json.dumps(note)
    assert "p_search" not in blob and "credits" not in blob.lower()


async def test_rerun_slice_after_finish_is_a_noop(harness: Harness) -> None:
    run = await start_run(harness)
    await run.scheduler.drain()
    calls = sum(p.calls for p in harness.providers.values())
    await run.scheduler.runner(run.tenant, run.run, 99)  # type: ignore[misc]
    assert sum(p.calls for p in harness.providers.values()) == calls


async def test_lying_generator_whole_run_persists_no_fabrication(harness: Harness) -> None:
    """W2: fabricated URLs, paraphrases, a grade field and the canary never reach a fact."""
    run = await start_run(harness, Script(lie=True))
    await run.scheduler.drain()
    urls = admin(
        "SELECT fe.url FROM fact_evidence fe JOIN facts f ON f.id = fe.fact_id"
        " WHERE f.account_id IN (SELECT account_id FROM account_runs WHERE id = %s)",
        run.run,
    )
    assert all("made-up.example" not in (u[0] or "") for u in urls)
    values = admin(
        "SELECT value_json::text FROM facts WHERE account_id IN"
        " (SELECT account_id FROM account_runs WHERE id = %s)",
        run.run,
    )
    assert all("CANARY" not in v[0] for v in values)
    drops = admin("SELECT count(*) FROM claim_drops WHERE run_id = %s", run.run)[0][0]
    assert drops > 0  # every lie is counted, never silent
    assert admin("SELECT status FROM account_runs WHERE id = %s", run.run)[0][0] == "PARTIAL"


async def test_a_run_with_confirmed_credits_never_waits_for_an_approval(
    harness: Harness,
) -> None:
    """The credits confirmed for the run are its cap (no "Continue?" checkpoint): even a tenant
    whose policy would not approve a cap runs straight through and settles once."""
    run = await start_run(harness, auto_approve=False)
    await run.scheduler.drain()
    assert admin("SELECT status FROM account_runs WHERE id = %s", run.run)[0][0] == "PARTIAL"
    assert run.run not in run.scheduler.timeouts  # no approval timeout was ever armed
    assert admin(
        "SELECT count(*) FROM account_runs WHERE id = %s AND awaiting_kind IS NOT NULL", run.run
    ) == [(0,)]
    assert settled_once(run.run)
