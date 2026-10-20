"""D-18 orchestration spike, hermetic: kill the worker five times, resume each time; two workers
on one run. GUARANTEE(test_killed_worker_resumes_without_double_payment)
GUARANTEE(test_two_workers_on_one_run_finish_once)

A subprocess worker dies with SIGKILL semantics (``os._exit``) right after the K-th provider
response, before the ledger records it. The resumed worker continues from the LangGraph
checkpoint. Guarantees checked: the run ends exactly like an uninterrupted run (PARTIAL,
builders_not_configured, every row reported); every paid call key is recorded once; a key is
re-issued at most once after a kill, and then flagged ``possible_duplicate``; the ledger sum
equals the run's spend; one settlement.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from account_intelligence.connectors.base import ProviderCode
from account_intelligence.connectors.settings import load_providers_config
from account_intelligence.platform.hermetic import LocalPayloadStore
from tests.connectors.conftest import FakeClock, FakeProvider, FakeSleep, Harness
from tests.system.world import LEDGER_SUM, Script, admin, create_only, drive, settled_once

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]
# the run lease from config (1500 s since slices became 1200 s, 2026-10-20; was 300 s)
LEASE_S = load_providers_config().defaults.ledger.lease_ttl_s


def _harness(payload_dir: Path) -> Harness:
    clock = FakeClock()
    return Harness(
        clock,
        FakeSleep(clock),
        {c: FakeProvider(clock) for c in ProviderCode},
        LocalPayloadStore(payload_dir),
        {},
    )  # type: ignore[arg-type]


def _kill(tenant: object, run: object, at: int, payloads: Path) -> int:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.system.kill_runner",
            str(tenant),
            str(run),
            str(at),
            str(payloads),
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        timeout=300,
    ).returncode


def _assert_final(run: object) -> None:
    ((status, reason),) = admin(
        "SELECT status, finished_reason FROM account_runs WHERE id = %s", run
    )
    assert (status, reason) == ("PARTIAL", "builders_not_configured")
    assert admin(
        "SELECT count(*) FROM coverage_rows WHERE run_id = %s AND status IS NULL", run
    ) == [(0,)]
    ledger = admin(LEDGER_SUM, run)
    spent = admin("SELECT spent_usd FROM account_runs WHERE id = %s", run)[0][0]
    assert Decimal(ledger[0][0]) == Decimal(spent)
    assert settled_once(run)


@pytest.mark.parametrize("kill_at", [1, 3, 5, 8, 12])
async def test_killed_worker_resumes_without_double_payment(tmp_path: Path, kill_at: int) -> None:
    payloads = tmp_path / "payloads"
    harness = _harness(payloads)
    tenant, run = await create_only(harness)
    assert _kill(tenant, run, kill_at, payloads) == 137  # died mid-run
    harness.clock.advance(LEASE_S + 100)  # the dead worker's lease expires
    await drive(harness, Script(), tenant, run)
    _assert_final(run)
    starts = admin(
        "SELECT args_hash, count(*) FROM tool_call_events WHERE run_id = %s AND event ="
        " 'started' GROUP BY args_hash",
        run,
    )
    assert all(n <= 2 for _, n in starts)  # at most one repeat per key after the kill
    repeated = {h for h, n in starts if n == 2}
    flagged = {
        h
        for (h,) in admin(
            "SELECT args_hash FROM tool_calls WHERE run_id = %s AND possible_duplicate", run
        )
    }
    assert repeated <= flagged | {
        h
        for (h,) in admin(
            "SELECT args_hash FROM tool_call_events WHERE run_id = %s AND event = 'failed'", run
        )
    }


async def test_two_workers_on_one_run_finish_once(tmp_path: Path) -> None:
    harness = _harness(tmp_path / "payloads")
    tenant, run = await create_only(harness)
    results = await asyncio.gather(
        drive(harness, Script(), tenant, run, worker_id="worker-a"),
        drive(_harness(tmp_path / "payloads"), Script(), tenant, run, worker_id="worker-b"),
        return_exceptions=True,
    )
    assert not [r for r in results if isinstance(r, BaseException)]
    # the loser yielded ("busy"); if it came back later it found the run finished (noop)
    harness.clock.advance(LEASE_S + 100)
    await drive(harness, Script(), tenant, run, worker_id="worker-c")
    _assert_final(run)
    keys = admin("SELECT args_hash, count(*) FROM tool_calls WHERE run_id = %s GROUP BY 1", run)
    assert all(n == 1 for _, n in keys)  # no paid call recorded twice
