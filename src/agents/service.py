"""Start, resume and inspect ABM runs (D-102, G14 to G17).

* ``start`` takes the tenant id from the verified token (an argument the API fills from the
  principal, never from the body), resolves the mode from the typed field only, links a build run
  to its pull parent and pins the config version; it is idempotent per ``(tenant, key)`` because
  the run id is derived from them.
* ``execute`` runs the graph on the run's checkpoint thread: a new run starts, an interrupted or
  crashed run continues from its last checkpoint (paid calls replay free from the ledger), and a
  finished run returns its final state without running anything again.
* ``resume`` answers an approval interrupt (cap, spend, sense-check pulls).
* ``build_option`` says whether "Phase 2 only" may be offered and shows the pull's date and
  coverage.

GUARANTEE(test_duplicate_start_is_idempotent)
GUARANTEE(test_build_with_a_foreign_or_missing_parent_fails_fast)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from langgraph.types import Command

from agents.errors import GuardViolation, InvalidMode, NoPulledData
from agents.graph import AbmDeps, AbmState, build_abm_graph
from agents.modes import RunMode, plan_run, resolve_mode
from agents.ports import RunRecord, RunStore
from agents.schemas import RunRequest

RUN_NAMESPACE = UUID("6f1c2d0e-7a51-4c55-9a0b-0ab000000001")


@dataclass(frozen=True)
class BuildOption:
    """What the start-run form shows for "Phase 2 only"."""

    pull_run_id: UUID
    pulled_at: datetime
    rows_stored: int
    rows_blocked: int
    config_version: str


def account_key(domain: str) -> str:
    """The account key: the normalised domain."""
    return domain.strip().lower().rstrip(".")


class AbmRunService:
    """See the module docstring."""

    def __init__(self, deps: AbmDeps, runs: RunStore, checkpointer: Any) -> None:
        self.deps, self.runs = deps, runs
        self.graph = build_abm_graph(deps).compile(checkpointer=checkpointer)

    async def start(
        self,
        tenant_id: UUID,
        request: RunRequest,
        idempotency_key: str,
        *,
        attended: bool,
        cap_request_micros: int = 0,
    ) -> RunRecord:
        """Create the run once (see the module docstring)."""
        mode = resolve_mode(request.mode)
        key = account_key(request.domain)
        parent = None
        if request.parent_run_id is not None:
            parent = await self.runs.get(tenant_id, request.parent_run_id)
            if parent is None or parent.account_key != key:
                raise NoPulledData(tuple(sorted(self.deps.cfg.plan)), "no pull run to build on")
        elif mode is RunMode.BUILD:
            raise NoPulledData(tuple(sorted(self.deps.cfg.plan)), "a build run names its pull run")
        plan = plan_run(
            mode,
            parent_run_id=parent.run_id if parent else None,
            parent_mode=resolve_mode(parent.mode) if parent else None,
            parent_config_version=parent.config_version if parent else None,
            requested_config_version=request.config_version,
            current_config_version=self.deps.cfg.config_version,
        )
        if plan.config_version != self.deps.cfg.config_version:
            raise GuardViolation(
                "G17", "config_version_unavailable", "choose the current version explicitly"
            )
        record = RunRecord(
            tenant_id=tenant_id,
            run_id=uuid.uuid5(RUN_NAMESPACE, f"{tenant_id}:{idempotency_key}"),
            mode=mode.value,
            parent_run_id=plan.parent_run_id,
            account_key=key,
            account_name=request.account_name.strip(),
            domain=key,
            config_version=plan.config_version,
            config_version_reason=plan.config_version_reason,
            attended=attended,
            cap_request_micros=cap_request_micros,
            acknowledged_stale=tuple(sorted(set(request.acknowledged_stale_rows))),
        )
        stored, _ = await self.runs.create(record, idempotency_key)
        if stored.mode != record.mode or stored.account_key != record.account_key:
            raise InvalidMode("idempotency key reused for a different request")
        return stored

    def _config(self, run_id: UUID) -> Any:
        return {"configurable": {"thread_id": str(run_id)}}

    async def execute(self, tenant_id: UUID, run_id: UUID) -> AbmState:
        """Run, continue or return the finished run (see the module docstring)."""
        record = await self.runs.get(tenant_id, run_id)
        if record is None:
            raise LookupError("run not found for this tenant")
        config = self._config(run_id)
        snapshot = await self.graph.aget_state(config)
        if snapshot.values.get("settled"):
            return cast(AbmState, snapshot.values)
        if snapshot.next:
            return cast(AbmState, await self.graph.ainvoke(None, config))
        initial: AbmState = {
            "tenant_id": str(record.tenant_id),
            "run_id": str(record.run_id),
            "mode": record.mode,
            "parent_run_id": str(record.parent_run_id) if record.parent_run_id else None,
            "account_key": record.account_key,
            "account_name": record.account_name,
            "domain": record.domain,
            "attended": record.attended,
            "config_version": record.config_version,
            "acknowledged_stale": list(record.acknowledged_stale),
            "cap_request_micros": record.cap_request_micros,
            "counters": {},
            "exits": {},
        }
        return cast(AbmState, await self.graph.ainvoke(initial, config))

    async def resume(self, tenant_id: UUID, run_id: UUID, approved: bool) -> AbmState:
        """Answer the pending approval interrupt."""
        if await self.runs.get(tenant_id, run_id) is None:
            raise LookupError("run not found for this tenant")
        return cast(
            AbmState, await self.graph.ainvoke(Command(resume=approved), self._config(run_id))
        )

    async def pending(self, tenant_id: UUID, run_id: UUID) -> list[dict[str, Any]]:
        """The interrupts the run is waiting on (empty when it is not paused)."""
        if await self.runs.get(tenant_id, run_id) is None:
            raise LookupError("run not found for this tenant")
        snapshot = await self.graph.aget_state(self._config(run_id))
        return [dict(i.value) for task in snapshot.tasks for i in task.interrupts]

    async def build_option(self, tenant_id: UUID, domain: str) -> BuildOption | None:
        """The newest usable pull for the account, or None (Phase 2 only is then not offered)."""
        manifest = await self.deps.manifests.latest(tenant_id, account_key(domain))
        if manifest is None:
            return None
        stored = sum(r.status == "stored" for r in manifest.rows)
        return BuildOption(
            manifest.run_id,
            manifest.created_at,
            stored,
            len(manifest.rows) - stored,
            manifest.config_version,
        )
