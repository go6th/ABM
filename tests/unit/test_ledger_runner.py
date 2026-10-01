"""Offline unit tests for ledgered_call and the runner slice loop.

Uses fakes for tenant_txn so the exactly-once ledger flow (I4) and the
checkpoint/resume semantics (ADR-002) are verified without a live database;
tests/spike/test_durability.py re-asserts the live path.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

import abm_generator.ledger as ledger_mod
import abm_generator.runner as runner_mod
from abm_generator.ledger import ledgered_call
from abm_generator.runner import (
    BudgetExceeded,
    SliceResult,
    SliceTimeout,
    execute_slice,
    run_to_completion,
)

TENANT = uuid4()


class FakeTxnStack:
    """Replaces tenant_txn with an in-memory store shared across the test."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], str] = {}  # (run_id, args_hash) -> response_json
        self.checkpoints: dict[UUID, str] = {}
        self.inserts: list[tuple[UUID, str]] = []
        self.vendor_calls = 0

    def txn(self, tenant_id: UUID) -> Any:
        store = self

        class _Ctx:
            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *exc: Any) -> None:
                return None

            async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
                from types import SimpleNamespace

                sql = str(stmt)
                p = params or {}
                if "FROM tool_calls" in sql:
                    hit = (p["r"], p["h"]) in store.rows
                    return SimpleNamespace(
                        first=lambda: (
                            SimpleNamespace(response_json=store.rows[(p["r"], p["h"])])
                            if hit
                            else None
                        )
                    )
                if "INSERT INTO tool_calls" in sql:
                    key = (p["r"], p["h"])
                    if key not in store.rows:  # ON CONFLICT DO NOTHING
                        store.rows[key] = p["resp"]
                        store.inserts.append(key)
                    return SimpleNamespace(first=lambda: None)
                if "FROM checkpoints_v0" in sql:
                    raw = store.checkpoints.get(p["r"])
                    return SimpleNamespace(
                        first=lambda: SimpleNamespace(value_json=raw) if raw else None
                    )
                if "INSERT INTO checkpoints_v0" in sql:
                    store.checkpoints[p["r"]] = p["v"]
                    return SimpleNamespace(first=lambda: None)
                raise AssertionError(f"unexpected SQL in fake: {sql}")

        return _Ctx()


@pytest.fixture()
def stack(monkeypatch: pytest.MonkeyPatch) -> FakeTxnStack:
    s = FakeTxnStack()
    monkeypatch.setattr(ledger_mod, "tenant_txn", s.txn)
    monkeypatch.setattr(runner_mod, "tenant_txn", s.txn)
    return s


async def test_ledgered_call_inserts_then_replays(stack: FakeTxnStack) -> None:
    async def vendor(**kwargs: Any) -> dict[str, Any]:
        stack.vendor_calls += 1
        return {"answer": "x", "cost_usd": 0.5}

    rid = uuid4()
    first = await ledgered_call(TENANT, rid, "broker:news", {"domain": "a.com"}, vendor)
    replay = await ledgered_call(TENANT, rid, "broker:news", {"domain": "a.com"}, vendor)

    assert stack.vendor_calls == 1, "vendor must be called exactly once (I4)"
    assert first == {"answer": "x", "cost_usd": 0.5}
    assert replay == {"answer": "x", "cost_usd": 0.5, "_replayed": True}
    assert len(stack.inserts) == 1


async def test_ledgered_call_explicit_cost_and_conflict(stack: FakeTxnStack) -> None:
    async def vendor(**kwargs: Any) -> dict[str, Any]:
        stack.vendor_calls += 1
        return {"ok": True}

    rid = uuid4()
    await ledgered_call(TENANT, rid, "tool", {"q": 1}, vendor, cost_usd=Decimal("1.25"))
    await ledgered_call(TENANT, rid, "tool", {"q": 1}, vendor)  # conflict -> replay
    assert stack.vendor_calls == 1
    assert len(stack.inserts) == 1


def _steps(*names: str) -> list[str]:
    return list(names)


async def test_execute_slice_completes_and_checkpoints(stack: FakeTxnStack) -> None:
    rid = uuid4()
    seen: list[str] = []

    async def step_a(state: Any) -> dict[str, Any]:
        seen.append("a")
        return {"facts": [{"k": "v"}], "spent_usd": 0.25}

    async def step_b(state: Any) -> dict[str, Any]:
        seen.append("b")
        return {}

    result = await execute_slice(TENANT, rid, _steps("a", "b"), {"a": step_a, "b": step_b})
    assert result.done and result.status == "COMPLETED"
    assert seen == ["a", "b"]
    saved = runner_mod.CheckpointState.from_json(stack.checkpoints[rid])
    assert saved.status == "COMPLETED"
    assert saved.facts == [{"k": "v"}]
    assert saved.spent_usd == 0.25
    assert saved.steps_done == ["a", "b"]


async def test_execute_slice_resumes_from_checkpoint(stack: FakeTxnStack) -> None:
    rid = uuid4()
    calls: list[str] = []

    async def step_a(state: Any) -> dict[str, Any]:
        calls.append("a")
        if len(calls) == 1:
            raise SliceTimeout  # simulate the kill between a and b
        return {}

    async def step_b(state: Any) -> dict[str, Any]:
        calls.append("b")
        return {}

    steps, fns = _steps("a", "b"), {"a": step_a, "b": step_b}
    first = await execute_slice(TENANT, rid, steps, fns)
    assert first.done is False  # timed out after checkpointing state
    second = await execute_slice(TENANT, rid, steps, fns)
    assert second.done and second.status == "COMPLETED"
    assert calls == ["a", "a", "b"], "step a re-ran after resume, b ran once"


async def test_execute_slice_stop_delta(stack: FakeTxnStack) -> None:
    rid = uuid4()

    async def boom(state: Any) -> dict[str, Any]:
        return {"stop": True, "status": "FAILED"}

    result = await execute_slice(TENANT, rid, _steps("boom"), {"boom": boom})
    assert result.done and result.status == "FAILED"
    saved = runner_mod.CheckpointState.from_json(stack.checkpoints[rid])
    assert saved.pending_steps == []


async def test_execute_slice_budget_exceeded(stack: FakeTxnStack) -> None:
    rid = uuid4()

    async def burn(state: Any) -> dict[str, Any]:
        raise BudgetExceeded

    result = await execute_slice(TENANT, rid, _steps("burn"), {"burn": burn})
    assert result.done and result.status == "BUDGET_EXCEEDED"


async def test_execute_slice_unknown_step_is_skipped(stack: FakeTxnStack) -> None:
    rid = uuid4()

    async def only(state: Any) -> dict[str, Any]:
        return {}

    result = await execute_slice(TENANT, rid, _steps("ghost", "only"), {"only": only})
    assert result.done and result.status == "COMPLETED"
    saved = runner_mod.CheckpointState.from_json(stack.checkpoints[rid])
    assert saved.steps_done == ["ghost", "only"], "unknown nodes pass through, never stall"


async def test_execute_slice_completed_run_is_short_circuited(stack: FakeTxnStack) -> None:
    rid = uuid4()
    state = runner_mod.CheckpointState(
        tenant_id=str(TENANT), run_id=str(rid), status="COMPLETED", steps_done=["a"]
    )
    stack.checkpoints[rid] = state.to_json()
    result = await execute_slice(TENANT, rid, _steps("a"), {})
    assert result.done and result.status == "COMPLETED"
    assert result.slices_used == 1


async def test_run_to_completion_slices_until_done(stack: FakeTxnStack) -> None:
    rid = uuid4()
    calls: list[str] = []

    async def step(state: Any) -> dict[str, Any]:
        calls.append("step")
        if len(calls) < 2:
            raise SliceTimeout
        return {}

    result = await run_to_completion(
        TENANT, rid, _steps("step"), {"step": step}, max_slices=3, slice_seconds=3600
    )
    assert result.done and result.status == "COMPLETED"
    assert len(calls) == 2


async def test_run_to_completion_hits_ceiling(stack: FakeTxnStack) -> None:
    rid = uuid4()

    async def step(state: Any) -> dict[str, Any]:
        raise SliceTimeout

    result = await run_to_completion(
        TENANT, rid, _steps("step"), {"step": step}, max_slices=2, slice_seconds=3600
    )
    assert result.done and result.status == "FAILED"
    assert result.error == "slice ceiling reached"


def test_slice_result_defaults() -> None:
    r = SliceResult(done=False)
    assert r.status == "RUNNING" and r.awaiting is False and r.error is None
