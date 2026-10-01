"""Time-sliced, resumable runner (Spike 2 / T11).

The checkpoint is the single source of truth (ADR-002). One slice runs at most
``slice_seconds``; the runner persists state after every step and resumes from the last
checkpoint. Combined with the tool ledger (I4) a killed process replays paid calls for
free. This module has no Inngest dependency: the orchestrator (ADR-003) is only the outer
loop that calls :func:`execute_slice`.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text

from abm_generator.db.session import tenant_txn

DEFAULT_SLICE_SECONDS = int(os.getenv("SLICE_SECONDS", "240"))
MAX_SLICES = int(os.getenv("MAX_SLICES", "60"))


@dataclass
class SliceResult:
    """Outcome of one slice execution."""

    done: bool
    status: str = "RUNNING"
    awaiting: bool = False
    slices_used: int = 0
    error: str | None = None


@dataclass
class CheckpointState:
    """Durable run state persisted after every step."""

    tenant_id: str
    run_id: str
    status: str = "RUNNING"
    steps_done: list[str] = field(default_factory=list)
    spent_usd: float = 0.0
    pending_steps: list[str] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> CheckpointState:
        data = json.loads(raw)
        return cls(**data)


async def _load_checkpoint(tenant_id: UUID, run_id: UUID) -> CheckpointState | None:
    async with tenant_txn(tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT value_json FROM checkpoints_v0 WHERE run_id = :r"),
                {"r": run_id},
            )
        ).first()
    return CheckpointState.from_json(row.value_json) if row else None


async def _save_checkpoint(tenant_id: UUID, state: CheckpointState) -> None:
    async with tenant_txn(tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO checkpoints_v0 (tenant_id, run_id, value_json, updated_at)"
                " VALUES ((SELECT app_tenant()), :r, CAST(:v AS jsonb), now())"
                " ON CONFLICT (tenant_id, run_id)"
                " DO UPDATE SET value_json = EXCLUDED.value_json, updated_at = now()"
            ),
            {"r": UUID(state.run_id), "v": state.to_json()},
        )


class SliceTimeout(Exception):
    """Raised when the slice time budget expires (resumes on the next slice)."""


class BudgetExceeded(Exception):
    """Raised by the in-graph budget guard (maps to BUDGET_EXCEEDED, Spec 5.6)."""


async def execute_slice(
    tenant_id: UUID,
    run_id: UUID,
    steps: list[str],
    step_fn: dict[str, Any],
    slice_seconds: int = DEFAULT_SLICE_SECONDS,
) -> SliceResult:
    """Run one bounded slice of the graph, resuming from the checkpoint.

    ``steps`` is the ordered node list; ``step_fn`` maps node name -> callable(state)
    returning a state delta dict. Every paid node must call through
    :func:`abm_generator.ledger.ledgered_call` so replays are free (I4).
    """
    state = await _load_checkpoint(tenant_id, run_id)
    if state is None:
        state = CheckpointState(tenant_id=str(tenant_id), run_id=str(run_id))
        state.pending_steps = list(steps)

    if state.status in {"COMPLETED", "PARTIAL", "FAILED", "CANCELED", "BUDGET_EXCEEDED"}:
        return SliceResult(done=True, status=state.status, slices_used=len(state.steps_done))
    if not state.pending_steps:
        state.status = "COMPLETED"
        await _save_checkpoint(tenant_id, state)
        return SliceResult(done=True, status="COMPLETED", slices_used=len(state.steps_done))

    deadline = time.monotonic() + slice_seconds

    async def _check_deadline() -> None:
        if time.monotonic() >= deadline:
            raise SliceTimeout

    try:
        while state.pending_steps:
            await _check_deadline()
            node = state.pending_steps[0]
            fn = step_fn.get(node)
            if fn is None:
                state.pending_steps.pop(0)
                state.steps_done.append(node)
                await _save_checkpoint(tenant_id, state)
                continue
            delta = await fn(state)
            if isinstance(delta, dict):
                if delta.get("stop"):
                    state.status = str(delta.get("status", "FAILED"))
                    state.pending_steps = []
                    await _save_checkpoint(tenant_id, state)
                    return SliceResult(
                        done=True, status=state.status, slices_used=len(state.steps_done)
                    )
                if "facts" in delta:
                    state.facts.extend(delta["facts"])
                if "spent_usd" in delta:
                    state.spent_usd += float(delta["spent_usd"])
            state.pending_steps.pop(0)
            state.steps_done.append(node)
            await _save_checkpoint(tenant_id, state)  # checkpoint per superstep (ADR-002)
        state.status = "COMPLETED"
        await _save_checkpoint(tenant_id, state)
    except SliceTimeout:
        await _save_checkpoint(tenant_id, state)
        return SliceResult(done=False, slices_used=len(state.steps_done))
    except BudgetExceeded:
        state.status = "BUDGET_EXCEEDED"
        state.pending_steps = []
        await _save_checkpoint(tenant_id, state)
        return SliceResult(done=True, status="BUDGET_EXCEEDED", slices_used=len(state.steps_done))

    return SliceResult(done=True, status=state.status, slices_used=len(state.steps_done))


async def run_to_completion(  # noqa: PLR0917 - orchestrator loop contract
    tenant_id: UUID,
    run_id: UUID,
    steps: list[str],
    step_fn: dict[str, Any],
    max_slices: int = MAX_SLICES,
    slice_seconds: int = DEFAULT_SLICE_SECONDS,
) -> SliceResult:
    """Drive slices until done or the ceiling is reached (orchestrator loop, ADR-003)."""
    result = SliceResult(done=False)
    for _ in range(max_slices):
        result = await execute_slice(tenant_id, run_id, steps, step_fn, slice_seconds)
        if result.done:
            break
    else:
        result = SliceResult(done=True, status="FAILED", error="slice ceiling reached")
    return result


__all__ = [
    "DEFAULT_SLICE_SECONDS",
    "MAX_SLICES",
    "BudgetExceeded",
    "CheckpointState",
    "SliceResult",
    "SliceTimeout",
    "execute_slice",
    "run_to_completion",
]
