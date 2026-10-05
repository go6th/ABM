"""Stage contract (workflow spec 4): the wrapper every graph node runs through.

* ``tenant_id`` and ``run_id`` must be in the state (set by the runner from the run row); a stage
  result may not contain them, nor raw text or prompts (W3, quarantine).
* idempotent: a stage already in ``completed_stages`` returns ``{}`` (a replay after a crash);
  its paid calls replay from the ledger anyway.
* every owned coverage row must be reported through the tracker: the handler returns
  ``coverage_reports`` with exactly the stage's rows, each the tracker's derived status and proof
  hash. Statuses are never typed here; the tracker derived them from ledger evidence (T1).
* a stage without a production handler raises ``NotConfigured`` (the runner ends the run PARTIAL
  with the reason, never a fake success).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from account_intelligence.platform.ports import NotConfigured
from account_intelligence.workflow.spec import StageSpec
from account_intelligence.workflow.state import RunState

FORBIDDEN_KEYS = frozenset({"tenant_id", "run_id", "page_text", "raw_text", "prompt"})
STATUSES = frozenset({"done", "partial", "not_done", "blocked", "none"})


class StageHandler(Protocol):
    """A stage body: small state in, a small result dict out."""

    async def __call__(self, state: RunState) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Stage:
    """A configured stage and its handler."""

    spec: StageSpec
    handler: StageHandler | None = None

    async def run(self, state: RunState) -> dict[str, Any]:
        """Run the handler under the contract above."""
        if not state.get("tenant_id") or not state.get("run_id"):
            raise ValueError(
                f"stage {self.spec.id} requires tenant_id and run_id from the trusted run path"
            )
        if self.spec.id in state.get("completed_stages", []):
            return {}
        if self.handler is None:
            raise NotConfigured(f"workflow stage {self.spec.id} has no production adapter")
        result = await self.handler(state)
        if FORBIDDEN_KEYS.intersection(result):
            raise ValueError(f"stage {self.spec.id} returned protected identity or raw text")
        reports = result.pop("coverage_reports", {})
        if set(reports) != set(self.spec.coverage):
            raise ValueError(f"stage {self.spec.id} must report each owned coverage row once")
        for row, report in reports.items():
            if not isinstance(report, dict) or report.get("status") not in STATUSES:
                raise ValueError(f"stage {self.spec.id} row {row} has no tracker status")
        updates = {row: {"status": r["status"]} for row, r in reports.items()}
        return {
            **result,
            "coverage_updates": updates,
            "completed_stages": [self.spec.id],
        }
