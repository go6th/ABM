"""Unit tests for the checkpoint state model (pure, no DB)."""

from __future__ import annotations

from abm_generator.runner import CheckpointState


def test_checkpoint_roundtrip() -> None:
    state = CheckpointState(
        tenant_id="t1", run_id="r1", pending_steps=["fetch", "distill"], spent_usd=1.5
    )
    restored = CheckpointState.from_json(state.to_json())
    assert restored == state
    assert restored.pending_steps == ["fetch", "distill"]
    assert restored.spent_usd == 1.5


def test_checkpoint_defaults() -> None:
    state = CheckpointState(tenant_id="t", run_id="r")
    assert state.status == "RUNNING"
    assert state.steps_done == []
    assert state.facts == []
