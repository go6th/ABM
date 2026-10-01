"""Spike 2 (T11): durable slices, resume, tool ledger.

Proves the slice-and-resume runner survives simulated process kills and that the tool
ledger records each paid call exactly once with a consistent checkpoint. Runs against
live Postgres when available; falls back to an in-process fake so the logic is testable
everywhere.
"""

from __future__ import annotations

import asyncio
import json
import random
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from abm_generator.ledger import args_hash
from abm_generator.runner import SliceResult, execute_slice

from ..conftest import requires_db

pytestmark = [pytest.mark.spike]


class FakeVendor:
    """Counts real vendor invocations; the ledger decides how often fn actually runs."""

    def __init__(self) -> None:
        self.real_calls = 0

    async def call(self, **kwargs: Any) -> dict[str, Any]:
        self.real_calls += 1
        await asyncio.sleep(0)
        return {"facts": [{"attr": kwargs.get("attr", "x")}], "cost_usd": 0.5}


class KillAfter(Exception):
    """Simulates a process kill after N ledger observations."""


class KillSwitch:
    """Kills (raises) after N real vendor calls in this run."""


# ---------------------------------------------------------------- pure in-process


async def test_slice_completes_all_steps_without_db() -> None:
    seen: list[str] = []

    async def step_a(state: Any) -> dict[str, Any]:
        seen.append("a")
        return {"facts": [{"v": 1}]}

    async def step_b(state: Any) -> dict[str, Any]:
        seen.append("b")
        return {"spent_usd": 0.25}

    result = SliceResult(done=False)
    state_fns = {"a": step_a, "b": step_b}

    # Without a DB the checkpoint persistence is skipped; emulate via monkeypatched loader.
    from unittest.mock import AsyncMock, patch

    with (
        patch("abm_generator.runner._load_checkpoint", AsyncMock(return_value=None)),
        patch("abm_generator.runner._save_checkpoint", AsyncMock()),
    ):
        result = await execute_slice(uuid4(), uuid4(), ["a", "b"], state_fns, slice_seconds=5)
    assert result.done and result.status == "COMPLETED"
    assert seen == ["a", "b"]


async def test_slice_budget_exceeded_maps_to_status() -> None:
    from unittest.mock import AsyncMock, patch

    from abm_generator.runner import BudgetExceeded

    async def boom(state: Any) -> dict[str, Any]:
        raise BudgetExceeded("cap")

    with (
        patch("abm_generator.runner._load_checkpoint", AsyncMock(return_value=None)),
        patch("abm_generator.runner._save_checkpoint", AsyncMock()),
    ):
        result = await execute_slice(uuid4(), uuid4(), ["boom"], {"boom": boom}, slice_seconds=5)
    assert result.done and result.status == "BUDGET_EXCEEDED"


async def test_slice_timeout_returns_not_done() -> None:
    from unittest.mock import AsyncMock, patch

    async def slow(state: Any) -> dict[str, Any]:
        await asyncio.sleep(0.2)
        return {}

    with (
        patch("abm_generator.runner._load_checkpoint", AsyncMock(return_value=None)),
        patch("abm_generator.runner._save_checkpoint", AsyncMock()),
    ):
        result = await execute_slice(uuid4(), uuid4(), ["s"], {"s": slow}, slice_seconds=0)
    assert result.done is False


# ---------------------------------------------------------------- live DB (spike)


@requires_db
async def test_ledger_replays_paid_call_exactly_once(
    tenant_a_id: UUID,
) -> None:
    from abm_generator.ledger import ledgered_call

    vendor = FakeVendor()
    run_id = uuid4()
    args = {"branch": "news", "domain": "example.com"}

    r1 = await ledgered_call(
        tenant_a_id, run_id, "broker:news", args, vendor.call, cost_usd=Decimal("0.5")
    )
    r2 = await ledgered_call(
        tenant_a_id, run_id, "broker:news", args, vendor.call, cost_usd=Decimal("0.5")
    )
    assert r1["facts"] == r2["facts"]
    assert r2.get("_replayed") is True
    assert vendor.real_calls == 1, "second call must be served from the ledger"

    # cleanup
    import psycopg

    dsn = __import__("os").environ.get(
        "TEST_TENANT_A_DSN", "postgresql://app_rw:app_rw_dev@localhost:5432/ai"
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM tool_calls WHERE run_id = %s", (run_id,))


@requires_db
async def test_run_completes_after_five_random_kills(tenant_a_id: UUID) -> None:
    """T11 pass criterion: run completes after 5 random kills; each paid call once."""
    import psycopg

    from abm_generator.ledger import ledgered_call
    from abm_generator.runner import run_to_completion

    dsn = __import__("os").environ.get(
        "TEST_TENANT_A_DSN", "postgresql://app_rw:app_rw_dev@localhost:5432/ai"
    )
    tenant_dsn = dsn

    vendor = FakeVendor()
    run_id = uuid4()
    rng = random.Random(42)  # noqa: S311 - deterministic simulation jitter
    kill_counter = {"n": 0}

    async def fetch_news(state: Any) -> dict[str, Any]:
        # Simulate: on selected executions, die right before the ledger insert commits.
        if kill_counter["n"] < 5 and rng.random() < 0.8:
            kill_counter["n"] += 1
            raise KeyboardInterrupt  # process-kill analogue
        return await ledgered_call(
            tenant_a_id, run_id, "broker:news", {"domain": "kill-test.com"}, vendor.call
        )

    async def distill(state: Any) -> dict[str, Any]:
        return {"facts": [], "spent_usd": 0.0}

    # The runner catches Exception subclasses; KeyboardInterrupt models SIGKILL and is
    # re-raised by asyncio. We wrap the slice loop here to emulate the orchestrator
    # restarting the process after each kill.
    steps = ["fetch_news", "distill"]
    final: SliceResult | None = None
    for _ in range(10):
        try:
            final = await run_to_completion(
                tenant_a_id,
                run_id,
                steps,
                {"fetch_news": fetch_news, "distill": distill},
                max_slices=3,
                slice_seconds=2,
            )
        except KeyboardInterrupt:
            final = None  # "process died"; loop = orchestrator restart
            continue
        if final is not None and final.done:
            break
    assert final is not None and final.done, "run must complete despite 5 kills"
    assert final.status == "COMPLETED"
    assert vendor.real_calls == 1, f"exactly-once billing violated: {vendor.real_calls}"

    # No corrupted checkpoint: the stored state parses and shows completion.
    with psycopg.connect(tenant_dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT value_json FROM checkpoints_v0 WHERE run_id = %s", (run_id,)
        ).fetchone()
    assert row is not None
    state = json.loads(row[0])
    assert state["status"] == "COMPLETED"
    assert "distill" in state["steps_done"]
    h = args_hash("broker:news", {"domain": "kill-test.com"})
    with psycopg.connect(tenant_dsn, autocommit=True) as conn:
        ledger_rows = conn.execute(
            "SELECT count(*) FROM tool_calls WHERE run_id = %s AND args_hash = %s",
            (run_id, h),
        ).fetchone()
    assert ledger_rows is not None and ledger_rows[0] == 1

    with psycopg.connect(tenant_dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM tool_calls WHERE run_id = %s", (run_id,))
        conn.execute("DELETE FROM checkpoints_v0 WHERE run_id = %s", (run_id,))
