"""Kill and resume in subprocesses (spec 9.5, K9, K12).

1. A worker dies between the reservation and the finalisation: on resume the stale intent is
   found, its reservation STAYS counted (never under-counted), it is written to
   ``unreconciled_usd`` and the report lists it as an open reserve with its amount.
2. A worker dies while the finishing transaction writes the report: nothing of the finish commits
   (no terminal state, no settlement, no half-written report); a resumed finish settles once and
   stores one report.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.runs import lifecycle
from account_intelligence.workflow.transitions import RunStatus
from spend import service
from spend.config import SpendConfig
from spend.models import Principal
from tests.fakes.spend import COSTS, PLAN, STAGES, FakeBalances
from tests.spend.conftest import NOW, admin
from tests.spend.test_connector_gate import OK
from tests.tracker.world import World, make_world, txn

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
WORKER = Path(__file__).resolve().parent / "kill_worker.py"
ROOT = Path(__file__).resolve().parents[2]


async def _world_with_cap(cfg: SpendConfig) -> World:
    w = make_world()
    admin("INSERT INTO wallets (tenant_id, balance) VALUES (%s, 100)", w.tenant)  # settlement
    async with txn(w) as tracker:
        await tracker.seed(w.run, w.tenant)
    p = await service.propose(
        tenant_txn, w.tenant, w.run, PLAN, COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES
    )
    human = Principal(kind="human", subject="u", tenant_id=str(w.tenant), role="admin")
    await service.approve(
        tenant_txn, w.tenant, w.run, p.version, p.proposal.content_hash, human, cfg, NOW
    )
    return w


def _kill(mode: str, w: World) -> int:
    """Run the doomed worker; its exit code (9 when it died where intended)."""
    done = subprocess.run(
        [sys.executable, str(WORKER), mode, str(w.tenant), str(w.run)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 9, done.stderr[-3000:]
    return done.returncode


async def test_a_kill_between_reserve_and_finalise_is_never_under_counted(
    cfg: SpendConfig,
) -> None:
    w = await _world_with_cap(cfg)
    assert _kill("claim", w) == 9
    spent = admin("SELECT spent_usd FROM account_runs WHERE id = %s", w.run)[0][0]
    assert spent == Decimal("0.005000")  # the reservation is counted while the intent is open
    # The worker stamps the intent with the real clock, so the resume time must be anchored to
    # the real clock too: a fixed fixture stamp rots once the calendar passes it and the 1s lease
    # would never look stale. NOW + 2 days only ever worked before 2026-10-05T12:00Z.
    later = max(NOW + timedelta(days=2), datetime.now(UTC) + timedelta(days=2))
    report, _, _ = await service.close_run(tenant_txn, w.tenant, w.run, cfg, later, 1)
    assert admin("SELECT spent_usd, unreconciled_usd FROM account_runs WHERE id = %s", w.run) == [
        (Decimal("0.005000"), Decimal("0.005000"))
    ]
    line = report.body["by_provider"][0]
    assert (line["open_reserves"], line["usd"]) == (1, "$0.005")
    assert report.body["reconciliation"] == ["consistent"]  # counted, flagged, not lost


async def test_a_kill_while_the_report_is_written_leaves_nothing_half_done(
    cfg: SpendConfig,
) -> None:
    w = await _world_with_cap(cfg)
    assert _kill("finish", w) == 9
    assert admin("SELECT status FROM account_runs WHERE id = %s", w.run) == [("RUNNING",)]
    assert admin("SELECT count(*) FROM spend_reports WHERE run_id = %s", w.run) == [(0,)]
    assert admin(
        "SELECT count(*) FROM wallet_ledger WHERE run_id = %s AND entry IN ('commit', 'refund')",
        w.run,
    ) == [(0,)]
    from tests.system.world import tracker

    for _ in range(2):  # the resumed finish, then a duplicate delivery
        async with tenant_txn(w.tenant) as s:
            await lifecycle.finish(
                s, tracker(s), w.run, RunStatus.CANCELED, reason="operator_cancel"
            )
    assert admin("SELECT status FROM account_runs WHERE id = %s", w.run) == [("CANCELED",)]
    assert admin("SELECT count(*) FROM spend_reports WHERE run_id = %s", w.run) == [(1,)]
    assert admin(
        "SELECT count(*) FROM wallet_ledger WHERE run_id = %s AND entry IN ('commit', 'refund')",
        w.run,
    ) == [(2,)]
