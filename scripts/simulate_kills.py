#!/usr/bin/env python3
"""Spike 2 harness: run a two-node graph and kill the process at random points.

Each iteration runs the graph in a fresh subprocess that is SIGKILLed after a random
delay. The parent then resumes the run; at the end it verifies that every ledgered paid
call happened exactly once and that the checkpoint is consistent.

Usage:
    python scripts/simulate_kills.py [--kills 5] [--dsn postgresql://postgres:dev@localhost:5432/ai]

Exit code 0 = pass criteria met (run completes; exactly-once ledger; consistent state).
"""

from __future__ import annotations

import argparse
import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path

CHILD_TEMPLATE = """\
import asyncio, json, os, sys
sys.path.insert(0, {src!r})
from uuid import UUID, uuid4
from abm_generator.ledger import ledgered_call
from abm_generator.runner import run_to_completion
from abm_generator.db.session import tenant_txn, dispose_engine
from sqlalchemy import text

TENANT = UUID({tenant!r})
RUN = UUID({run!r})

calls = {{"n": 0}}

async def fetch(state):
    if calls["n"] == 0:
        calls["n"] += 1
        async def vendor(**kw):
            await asyncio.sleep(0.01)
            return {{"facts": [{{"who": "cto"}}], "cost_usd": 0.5}}
        return await ledgered_call(TENANT, RUN, "broker:news", {{"domain": "sim.com"}}, vendor)
    return {{"facts": []}}

async def distill(state):
    return {{"facts": [], "spent_usd": 0.0}}

async def main():
    result = await run_to_completion(
        TENANT, RUN, ["fetch", "distill"],
        {{"fetch": fetch, "distill": distill}}, max_slices=2, slice_seconds=2)
    await dispose_engine()
    print(json.dumps({{"status": result.status, "done": result.done}}))

asyncio.run(main())
"""


def _psql(dsn: str, sql: str, args: tuple[object, ...] = ()) -> list[tuple]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        cur = conn.execute(sql, args)
        return cur.fetchall() if cur.description else []


def _uuid() -> object:
    from uuid import uuid4

    return uuid4()


def _seed(dsn: str) -> tuple[str, str]:
    tenant, run = str(_uuid()), str(_uuid())
    _psql(dsn, "INSERT INTO tenants (id, name) VALUES (%s, 'kill-sim')", (tenant,))
    _psql(dsn, "INSERT INTO wallets (tenant_id, balance) VALUES (%s, 100)", (tenant,))
    _psql(
        dsn,
        "INSERT INTO accounts (id, tenant_id, domain) VALUES (%s, %s, 'sim.com')",
        (str(_uuid()), tenant),
    )
    acc = _psql(dsn, "SELECT id FROM accounts WHERE tenant_id = %s", (tenant,))[0][0]
    _psql(
        dsn,
        "INSERT INTO account_runs (id, tenant_id, account_id, graph_version, budget_json,"
        " reserved_credits, idempotency_key) VALUES (%s, %s, %s, 'sim', '{}', 5, %s)",
        (run, tenant, acc, f"sim-{run}"),
    )
    return tenant, run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kills", type=int, default=5)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("TEST_MIGRATOR_DSN", "postgresql://postgres:dev@localhost:5432/ai"),
    )
    parser.add_argument(
        "--app-dsn",
        default=os.environ.get(
            "TEST_TENANT_A_DSN", "postgresql://app_rw:app_rw_dev@localhost:5432/ai"
        ),
    )
    options = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    src = str(root / "src")

    tenant, run = _seed(options.dsn)
    rng = random.Random(2026)  # noqa: S311 - deterministic simulation jitter
    child = CHILD_TEMPLATE.format(src=src, tenant=tenant, run=run)

    completed = False
    for attempt in range(options.kills + 1):
        proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-c", child],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        delay = rng.uniform(0.05, 1.2)
        time.sleep(delay)
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
            proc.wait()
            print(f"[sim] attempt {attempt}: killed after {delay:.2f}s")
        else:
            out, _ = proc.communicate()
            print(f"[sim] attempt {attempt}: finished on its own: {out.strip()}")
            completed = True
            break

    if not completed:
        # Final clean resume without killing.
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-c", child], capture_output=True, text=True, timeout=60, check=False
        )
        print(f"[sim] final resume: {proc.stdout.strip() or proc.stderr[-200:]}")
        completed = '"done": true' in proc.stdout or "'done': True" in proc.stdout

    # Verify exactly-once ledger and consistent checkpoint.
    ledger = _psql(
        options.dsn,
        "SELECT count(*) FROM tool_calls WHERE tool = 'broker:news'"
        " AND response_json::text LIKE '%sim.com%'",
    )
    calls = ledger[0][0] if ledger else 0
    cp = _psql(
        options.dsn,
        "SELECT value_json::json->>'status' FROM checkpoints_v0 WHERE run_id = %s",
        (run,),
    )
    status = cp[0][0] if cp else None

    print(f"[sim] ledger broker:news rows = {calls} (expect 1)")
    print(f"[sim] checkpoint status = {status}")
    ok = completed and calls == 1 and status == "COMPLETED"

    _psql(options.dsn, "DELETE FROM checkpoints_v0 WHERE run_id = %s", (run,))
    _psql(options.dsn, "DELETE FROM tool_calls WHERE run_id = %s", (run,))
    _psql(options.dsn, "DELETE FROM account_runs WHERE id = %s", (run,))
    _psql(options.dsn, "DELETE FROM accounts WHERE tenant_id = %s", (tenant,))
    _psql(options.dsn, "DELETE FROM wallet_ledger WHERE tenant_id = %s", (tenant,))
    _psql(options.dsn, "DELETE FROM wallets WHERE tenant_id = %s", (tenant,))
    _psql(options.dsn, "DELETE FROM tenants WHERE id = %s", (tenant,))

    print("[sim] RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
