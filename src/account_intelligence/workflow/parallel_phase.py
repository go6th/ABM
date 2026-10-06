"""Parallel research phase (Parallel execution v1, D-42 to D-47): the graph's ``research`` node runs
the research stages as units on the dependency-aware scheduler instead of one after another.

* Units come from ``config/parallel.yaml`` through ``parallel.units.body_plan``: today's stage
  handlers are stage-granular, so each stage is one composite unit over the coarsened PRD graph
  (pass 1, baseline, news, jobs and people start together; technology waits for the postings,
  person-name searches for the people list, Lead verification for every pull, softly).
* The stage code keeps its logic and becomes the unit body (``Stage.run``: its contract and its
  tracker reports are unchanged). Unit results are merged by unit key with the checkpoint state's
  own reducers (D-42); a unit's input is the phase input plus its dependencies' results only.
* Funding comes from the credit manager (``spend.limits``): ``priority_only`` past the soft
  limit, ``paused`` while a pause request is open, ``stopped`` after a decline or timeout. A unit
  the cap stopped (``counters.budget_exhausted``) opens the pause request of the current cap
  version (``spend.pause.open_pause``); the node then returns a pause marker and the graph routes
  to ``cap_gate``, whose ``interrupt`` is the one owner of the wait (the runner moves the run to
  AWAITING_APPROVAL with ``awaiting_kind = 'cap'``). After the decision the graph comes back here
  and the ready set is re-derived from the unit events.
* A retryable unit failure is re-raised for the node ``RetryPolicy`` (the only retry owner); a unit
  that will not run has its rows recorded through the tracker with the reason (never silent).

GUARANTEE(test_parallel_whole_run_completes_with_the_same_rows_as_sequential): system test.
GUARANTEE(test_cap_pause_interrupts_and_resumes_after_approval): system test.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy import text

from account_intelligence.tracker.config import load_checklist
from account_intelligence.tracker.model import Actor, ReasonCode
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.stage import Stage, StageHandler
from account_intelligence.workflow.state import RunState, append_unique, merge_maps, sum_counters
from parallel.config import ParallelConfig, load_parallel_config
from parallel.executor import BodyOutcome, Executor
from parallel.merge import Reducer
from parallel.models import Durations, ExitReason, FundingMode, WorkUnit
from parallel.scheduler import Deadline
from parallel.store import PgEventStore, measured_durations
from parallel.units import body_plan

RESEARCH_NODE = "research"
CAP_GATE_NODE = "cap_gate"
REDUCERS: dict[str, Reducer] = {
    "completed_stages": append_unique,
    "references": merge_maps,
    "coverage_updates": merge_maps,
    "counters": sum_counters,
    "loop_exits": merge_maps,
}
UNRUN_REASON = {
    "dependency_failed": ReasonCode.DEPENDENCY_FAILED,
    "deadline": ReasonCode.DEADLINE,
    "budget_cap": ReasonCode.BUDGET_CAP,
    "source_failed": ReasonCode.SOURCE_FAILED,
}


def research_stages(cfg: ParallelConfig | None = None) -> tuple[str, ...]:
    """The stage ids the parallel phase runs (the configured bodies)."""
    return tuple(sorted((cfg or load_parallel_config()).bodies))


def high_bodies(deps: RunDeps, cfg: ParallelConfig) -> frozenset[str]:
    """Bodies whose stage owns at least one High tracker row (funded first, D-46)."""
    priority = {r.key: r.priority for r in load_checklist().rows}
    specs = {s.id: s for s in deps.spec.stages}
    return frozenset(
        name
        for name in cfg.bodies
        if name in specs and any(priority.get(row) == "high" for row in specs[name].coverage)
    )


class WorkflowHooks:
    """The executor's ``PhaseHooks`` for one run."""

    def __init__(self, deps: RunDeps, units: Mapping[str, WorkUnit]) -> None:
        self.deps = deps
        self.units = units
        self.specs = {s.id: s for s in deps.spec.stages}
        self.denied: list[WorkUnit] = []

    async def funding(self) -> FundingMode:
        """The credit manager's mode for the run; once the cap has denied a unit in this pass, no
        new paid unit launches (M3) until the pause request is decided."""
        from spend.limits import funding_mode

        if self.denied:
            return FundingMode.PAUSED
        async with self.deps.txn(self.deps.tenant_id) as s:
            return FundingMode((await funding_mode(s, self.deps.run_id)).value)

    async def on_cap_denied(self, unit: WorkUnit) -> None:
        """Remember the unit; the request is opened once the pass has drained (``open_request``)
        so it covers every unit the cap blocked."""
        if unit.key not in {u.key for u in self.denied}:
            self.denied.append(unit)

    async def open_request(self) -> bool:
        """Open (idempotently) the pause request of the current cap version for every unit the cap
        blocked in this pass (M3, M4). False when policy declined it at once (or there is no
        approved cap): the run then continues without paid work and ends BUDGET_EXCEEDED."""
        from spend import pause
        from spend.config import load_spend_config, load_v2_config
        from spend.limits import read_inputs

        v2, spend_cfg = load_v2_config(), load_spend_config()
        now = self.deps.clock.now()
        async with self.deps.txn(self.deps.tenant_id) as s:
            inputs = await read_inputs(s, self.deps.run_id)
            if inputs is None or inputs.cap_version is None or inputs.cap_micros is None:
                return False  # no approved cap: the K2 path (no paid call) owns this run
            blocked = []
            for unit in sorted(self.denied, key=lambda u: u.key):
                spec = self.specs.get(unit.body)
                rows = tuple(spec.coverage) if spec else ()
                estimate = unit.est_cost_micros
                if estimate is None:
                    estimate = await _upper_bound_from_proposal(s, self.deps.run_id, rows)
                blocked.append(pause.BlockedUnit(unit.key, estimate, rows))
            request = pause.compute_request(
                inputs.cap_version,
                inputs.cap_micros,
                inputs.committed_micros,
                inputs.held_micros,
                blocked,
                spend_cfg.rounding_step_micros,
                spend_cfg.sample_hold_micros,
                v2.tenant_ceiling_micros,
            )
            _, opened = await pause.open_pause(s, self.deps.run_id, request, now, v2)
        return opened

    async def on_unrun(self, unit: WorkUnit, reason: str) -> Mapping[str, Any]:
        """Record every row of a unit that will not run, with its reason, through the tracker."""
        spec = self.specs.get(unit.body)
        if spec is None:
            return {}
        code = UNRUN_REASON.get(reason, ReasonCode.SOURCE_FAILED)
        updates: dict[str, Any] = {}
        async with self.deps.txn(self.deps.tenant_id) as s:
            tracker = self.deps.tracker(s)
            for row in spec.coverage:
                try:
                    result = await tracker.record_outcome(
                        self.deps.tenant_id,
                        self.deps.run_id,
                        row,
                        [],
                        Actor.STAGE,
                        not_attempted_reason=code,
                        stage=spec.id,
                    )
                except Exception:  # a row the stage already settled keeps its status
                    continue
                updates[row] = {"status": result.status.value}
        return {"coverage_updates": updates, "completed_stages": [spec.id]}

    async def run_cancelled(self) -> bool:
        """Whether the run was cancelled."""
        async with self.deps.txn(self.deps.tenant_id) as s:
            status = (
                await s.execute(
                    text("SELECT status FROM account_runs WHERE id = :r"), {"r": self.deps.run_id}
                )
            ).scalar_one_or_none()
        return status == "CANCELED"


async def _upper_bound_from_proposal(s: Any, run_id: Any, rows: tuple[str, ...]) -> int | None:
    """A composite unit's upper-bound cost: the high estimates of the first approved proposal's
    breakdown lines serving its rows (D-29 breakdown). None when a line is unknown or none
    matches (the pause then counts the unit at the sample hold, never at zero)."""
    proposal: Any = (
        await s.execute(
            text(
                "SELECT proposal_json FROM run_cap_versions WHERE run_id = :r"
                " AND NOT (proposal_json ? 'extension') ORDER BY version DESC LIMIT 1"
            ),
            {"r": run_id},
        )
    ).scalar_one_or_none()
    lines = [
        line
        for line in (proposal or {}).get("lines", [])
        if set(line.get("row_keys") or ()) & set(rows)
    ]
    if not lines or any(line.get("status") != "priced" for line in lines):
        return None
    return sum(int(line["high_micros"]) for line in lines)


def _bodies(handlers: Mapping[str, StageHandler], deps: RunDeps) -> dict[str, Any]:
    specs = {s.id: s for s in deps.spec.stages}

    def make(stage_id: str) -> Any:
        stage = Stage(specs[stage_id], handlers.get(stage_id))

        async def body(unit: WorkUnit, state: Mapping[str, Any]) -> BodyOutcome:
            result = await stage.run(dict(state))  # type: ignore[arg-type]
            capped = bool((result.get("counters") or {}).get("budget_exhausted"))
            return BodyOutcome(result, cap_denied=capped)

        return body

    return {name: make(name) for name in specs if name in handlers}


async def _deadline(deps: RunDeps, cfg: ParallelConfig) -> Deadline:
    async with deps.txn(deps.tenant_id) as s:
        created: Any = (
            await s.execute(
                text("SELECT created_at FROM account_runs WHERE id = :r"), {"r": deps.run_id}
            )
        ).scalar_one()
    return Deadline(created + timedelta(seconds=cfg.deadline_ceiling_s), cfg.tail_reserve_s)


def research_node(
    handlers: Mapping[str, StageHandler], deps: RunDeps, cfg: ParallelConfig | None = None
) -> Any:
    """The ``research`` graph node (see the module docstring)."""
    cfg = cfg or load_parallel_config()

    async def research(state: RunState) -> dict[str, Any]:
        """Run (or resume) the research units; a pause marker, the merged update, or a retry."""
        from account_intelligence.workflow.stages import spend as spend_stage

        done = set(state.get("completed_stages") or [])
        if all(name in done for name in cfg.bodies):
            return {}
        extra = await spend_stage.after_approval(deps, state)
        base: dict[str, Any] = dict(state)
        if extra:
            base["references"] = {**(base.get("references") or {}), **extra["references"]}
        units = body_plan(cfg, high=high_bodies(deps, cfg)).units
        worker = deps.connectors.worker_id if deps.connectors is not None else "worker"
        store = PgEventStore(deps.txn, deps.tenant_id, deps.run_id, worker)
        async with deps.txn(deps.tenant_id) as s:
            durations: Durations = await measured_durations(s, cfg)
        deadline = await _deadline(deps, cfg)
        extra_refs = dict(extra.get("references") or {}) if extra else {}
        # LOOP(research passes): cap=2 passes (a pass that paused but whose request policy declined
        # at once runs again with paid work stopped); exit=converged or another exit, pause marker
        for _ in range(2):
            hooks = WorkflowHooks(deps, {u.key: u for u in units})
            executor = Executor(
                store,
                _bodies(handlers, deps),
                hooks,
                _Clock(deps),
                REDUCERS,
                max_in_flight=cfg.max_in_flight_units,
                unit_attempts=cfg.unit_attempts,
                stall_wait_s=cfg.providers.lease_poll_s,
            )
            result = await executor.run_phase(units, base, deadline, durations)
            if result.exit is ExitReason.RETRYABLE and result.error is not None:
                raise result.error  # the node RetryPolicy owns the retry (W6)
            if result.exit is not ExitReason.PAUSED:
                break
            if await hooks.open_request():
                out: dict[str, Any] = {"counters": {"cap_pauses_seen": 1}}
                if extra_refs:
                    out["references"] = extra_refs
                return out
        update = dict(result.update)
        update["references"] = {**(update.get("references") or {}), **extra_refs}
        update["loop_exits"] = {**(update.get("loop_exits") or {}), "research": result.exit.value}
        return update

    return research


async def cap_gate(state: RunState) -> dict[str, Any]:
    """Wait for the pause decision (the workflow's interrupt owns the wait, D-45). The decision
    itself is read from the database by the next research pass, never from the resume value."""
    from langgraph.types import interrupt

    interrupt({"awaiting": "cap"})
    return {"counters": {"cap_gates_passed": 1}}


def after_research(state: RunState) -> str:
    """Route to ``cap_gate`` while a pause is unanswered, else on to the sequential tail."""
    counters = state.get("counters") or {}
    if int(counters.get("cap_pauses_seen", 0)) > int(counters.get("cap_gates_passed", 0)):
        return CAP_GATE_NODE
    return "next"


class _Clock:
    """The executor clock over the run's clock (wall time, monotonic, sleep)."""

    def __init__(self, deps: RunDeps) -> None:
        self.deps = deps

    def now(self) -> Any:
        """Wall time."""
        return self.deps.clock.now()

    def monotonic(self) -> float:
        """Monotonic seconds (the slice deadline is the runner's asyncio timeout, not this)."""
        import time

        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        """Wait."""
        import asyncio

        await asyncio.sleep(seconds)
