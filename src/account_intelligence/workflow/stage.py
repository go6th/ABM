"""Stage execution contract and fail-closed default adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from account_intelligence.db.enums import CoverageStatus
from account_intelligence.workflow.coverage import CoverageProof
from account_intelligence.workflow.ports import NotConfigured
from account_intelligence.workflow.spec import StageSpec
from account_intelligence.workflow.state import RunState


class StageHandler(Protocol):
    async def __call__(self, state: RunState) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Stage:
    spec: StageSpec
    handler: StageHandler | None = None

    async def run(self, state: RunState) -> dict[str, Any]:
        if not state.get("tenant_id") or not state.get("run_id"):
            raise ValueError(
                f"stage {self.spec.id} requires tenant_id and run_id from the trusted run path"
            )
        if self.spec.id in state.get("completed_stages", []):
            return {}
        if self.handler is None:
            raise NotConfigured(f"workflow stage {self.spec.id} has no production adapter")
        result = await self.handler(state)
        forbidden = {"tenant_id", "run_id", "page_text", "raw_text", "prompt"}
        if forbidden.intersection(result):
            raise ValueError(f"stage {self.spec.id} returned protected identity or raw text")
        updates = result.get("coverage_updates", {})
        if set(updates) != set(self.spec.coverage):
            raise ValueError(
                f"stage {self.spec.id} must report each owned coverage row exactly once"
            )
        for row, update in updates.items():
            if not isinstance(update, dict) or not update.get("status") or not update.get("proof"):
                raise ValueError(f"stage {self.spec.id} coverage row {row} lacks status or proof")
            CoverageStatus(update["status"])
            CoverageProof(**update["proof"]).validate()
        return {**result, "completed_stages": [self.spec.id]}
