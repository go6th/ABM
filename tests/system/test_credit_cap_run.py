"""Credit cap through whole runs (spec 7.1, 9.7): setup fails closed and waits for a person with no
paid call; a run inside its cap raises no second interrupt (K11); every terminal path, an approval
timeout included, stores exactly one spend report and one settlement in the finishing transaction
(K12); the tenant view passes the skill's client-output scanner (K13)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.runs import lifecycle
from account_intelligence.workflow.transitions import RunStatus
from tests.system.world import Harness, admin, start_run, tracker

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]
SCANNER = ROOT / ".claude/skills/account-intelligence-platform/scripts/scan_client_output.py"


def _one_report_one_settlement(run: object) -> None:
    assert admin("SELECT count(*) FROM spend_reports WHERE run_id = %s", run) == [(1,)]
    entries = admin(
        "SELECT entry FROM wallet_ledger WHERE run_id = %s AND entry IN"
        " ('commit', 'refund') ORDER BY entry",
        run,
    )
    assert entries == [("commit",), ("refund",)]


async def test_setup_without_policy_waits_and_makes_no_paid_call(harness: Harness) -> None:
    run = await start_run(harness, auto_approve=False)
    await run.scheduler.drain()
    assert admin("SELECT status FROM account_runs WHERE id = %s", run.run) == [
        ("AWAITING_APPROVAL",)
    ]
    assert admin("SELECT count(*) FROM tool_call_events WHERE run_id = %s", run.run) == [(0,)]
    assert admin("SELECT event FROM run_cap_events WHERE run_id = %s", run.run) == [("proposed",)]
    assert admin("SELECT cap_version FROM account_runs WHERE id = %s", run.run) == [(None,)]


async def test_a_run_within_its_cap_has_one_approval_and_no_interrupt(harness: Harness) -> None:
    run = await start_run(harness, auto_approve=True)
    await run.scheduler.drain()
    status = admin("SELECT status, spent_usd, budget_usd FROM account_runs WHERE id = %s", run.run)[
        0
    ]
    assert status[0] in ("COMPLETED", "PARTIAL") and status[1] <= status[2]
    events = [
        e
        for (e,) in admin("SELECT event FROM run_cap_events WHERE run_id = %s ORDER BY id", run.run)
    ]
    assert events == ["proposed", "approved"]  # one approval, never an extension request
    _one_report_one_settlement(run.run)


async def test_an_approval_timeout_cancels_settles_and_reports_once(harness: Harness) -> None:
    run = await start_run(harness, auto_approve=False)
    await run.scheduler.drain()
    later = datetime.now(UTC) + timedelta(days=2)
    async with tenant_txn(run.tenant) as s:
        assert await lifecycle.expire_approval(s, tracker(s), run.run, later)
    assert admin("SELECT status FROM account_runs WHERE id = %s", run.run) == [("CANCELED",)]
    _one_report_one_settlement(run.run)
    # the pending cap proposal is closed with the run (one decision, K3)
    assert admin(
        "SELECT event, reason FROM run_cap_events WHERE run_id = %s ORDER BY id", run.run
    ) == [("proposed", None), ("expired", "run_ended")]
    async with tenant_txn(run.tenant) as s:  # a duplicate delivery changes nothing
        await lifecycle.finish(s, tracker(s), run.run, RunStatus.CANCELED, reason="again")
    _one_report_one_settlement(run.run)


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
