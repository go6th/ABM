"""Credit cap through whole runs (spec 7.1, 9.7, replica credit model): the credits confirmed on the
client's pool are the run's cap, so a run never waits for a cap approval and never interrupts
(K11); it pays only within its budget; every terminal path, a cancel included, stores exactly one
spend report and one settlement in the finishing transaction (K12); the tenant view passes the
skill's client-output scanner (K13)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.runs import lifecycle
from account_intelligence.workflow.transitions import RunStatus
from tests.system.world import Harness, admin, create_only, settled_once, start_run, tracker

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]
SCANNER = ROOT / ".claude/skills/account-intelligence-platform/scripts/scan_client_output.py"


def _one_report_one_settlement(run: object) -> None:
    assert admin("SELECT count(*) FROM spend_reports WHERE run_id = %s", run) == [(1,)]
    assert settled_once(run)


async def test_a_run_without_an_approval_policy_runs_within_its_credits(harness: Harness) -> None:
    run = await start_run(harness, auto_approve=False)
    await run.scheduler.drain()
    status, spent, budget = admin(
        "SELECT status, spent_usd, budget_usd FROM account_runs WHERE id = %s", run.run
    )[0]
    assert status in ("COMPLETED", "PARTIAL") and spent <= budget
    assert admin("SELECT count(*) FROM run_cap_events WHERE run_id = %s", run.run) == [(0,)]
    _one_report_one_settlement(run.run)


async def test_a_run_within_its_credits_has_no_interrupt(harness: Harness) -> None:
    run = await start_run(harness, auto_approve=True)
    await run.scheduler.drain()
    status = admin("SELECT status, spent_usd, budget_usd FROM account_runs WHERE id = %s", run.run)[
        0
    ]
    assert status[0] in ("COMPLETED", "PARTIAL") and status[1] <= status[2]
    assert run.run not in run.scheduler.timeouts  # no approval was ever waited for
    _one_report_one_settlement(run.run)


async def test_a_cancel_settles_and_reports_once(harness: Harness) -> None:
    t, run = await create_only(harness)
    async with tenant_txn(t) as s:
        await lifecycle.finish(s, tracker(s), run, RunStatus.CANCELED, reason="user_cancel")
    assert admin("SELECT status FROM account_runs WHERE id = %s", run) == [("CANCELED",)]
    _one_report_one_settlement(run)
    assert admin("SELECT status, credits_used FROM research_jobs WHERE run_id = %s", run) == [
        ("canceled", 0)
    ]
    async with tenant_txn(t) as s:  # a duplicate delivery changes nothing
        await lifecycle.finish(s, tracker(s), run, RunStatus.CANCELED, reason="again")
    _one_report_one_settlement(run)


async def test_the_tenant_view_passes_the_client_output_scanner(
    harness: Harness, tmp_path: Path
) -> None:
    run = await start_run(harness, auto_approve=True)
    await run.scheduler.drain()
    tenant_json, operator_json = admin(
        "SELECT tenant_json, operator_json FROM spend_reports WHERE run_id = %s", run.run
    )[0]
    assert set(tenant_json) == {"run_id", "status", "categories", "total_calls", "amounts_shown"}
    assert tenant_json["amounts_shown"] is False  # no amounts until D-04 gives a credit price
    assert "p_" not in json.dumps(tenant_json) and "$" not in json.dumps(tenant_json)
    view = tmp_path / "tenant_view.json"
    view.write_text(json.dumps(tenant_json, sort_keys=True))
    done = subprocess.run(
        [sys.executable, str(SCANNER), str(view)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stdout
    # the operator report names provider codes: it is internal and never a client file
    assert "by_provider" in operator_json
    notes = [k for k in harness.payloads.objects if "/delivery/" in k]
    assert notes, "the delivery note was stored"
    note = (await harness.payloads.get(notes[0])).decode()
    for internal in ("p_search", "p_scrape", "spent", "budget", "credit", "$"):
        assert internal not in note, internal  # D-30: spend stays out of client output
