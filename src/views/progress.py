"""Run progress for the client (VW5): stages done of stages, never an ETA, a provider, a credit
count, a cost, a call reference or another account. Pure.

A research stage owns coverage rows (``config/workflow_v1.yaml``); it is done when every row it
owns has a status (done, partial, not done, blocked or none: rule 10). A stage that owns no rows
(setup) is done once any later stage has settled a row, or when the run has ended. A finished run
shows every stage done whatever the rows say (a cancelled or failed run says so in its status).

``steps`` lists each step with its state for the run screen's step list: done, running, paused (it
waits for approval), waiting (not started), stopped (the run ended there) or not_reached (the run
ended before it). A run on the ABM research graph (``agents.graph``) reports its own steps: the
graph nodes it has finished, read from its checkpoint (``ABM_STEPS``), because that graph reports
every coverage row at once, in one step, and the stage counts alone would sit still for most of
the run.

GUARANTEE(test_progress_shows_stage_counts_only)
GUARANTEE(test_progress_follows_the_research_graph_step_by_step)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field

from account_intelligence.runs.transitions import TERMINAL

# What the client sees for each run status (no internal state name leaks).
STATUS_LABELS: Mapping[str, str] = {
    "QUEUED": "Queued",
    "RUNNING": "Researching",
    "AWAITING_APPROVAL": "Waiting for approval",
    "DISTILLING": "Writing up",
    "VERIFYING": "Checking",
    "COMPLETED": "Complete",
    "PARTIAL": "Complete with gaps",
    "BUDGET_EXCEEDED": "Stopped at its limit",
    "FAILED": "Stopped",
    "CANCELED": "Cancelled",
}


# The ABM research graph's nodes as the client's eight steps, in order (``finish`` settles and ends
# every run, whatever happened before it, so it belongs to no step).
ABM_STEPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("setup", "Set up", ("setup", "cap_approval")),
    ("search", "Web research", ("pull", "spend_gate", "load_manifest")),
    ("read", "Reading and grading", ("interpret",)),
    ("follow_up", "Follow-up searches", ("sense_check", "sense_approval", "targeted_pulls")),
    ("write", "Writing up", ("distill",)),
    ("build", "Building files", ("build",)),
    ("gate", "Quality check", ("gate",)),
    ("deliver", "Delivery", ("deliver",)),
)
ABM_NODES: frozenset[str] = frozenset(n for _, _, nodes in ABM_STEPS for n in nodes) | {"finish"}
# Nodes only the ABM graph has (the older workflow graph also has setup, build, gate, deliver).
ABM_ONLY: frozenset[str] = frozenset({"cap_approval", "pull", "load_manifest", "interpret"})
WORKING = {"RUNNING", "DISTILLING", "VERIFYING"}


@dataclass(frozen=True)
class Step:
    """One step of the run as the client sees it."""

    id: str
    name: str
    state: str
    rows_done: int = 0
    rows_total: int = 0


@dataclass(frozen=True)
class Progress:
    """One run's progress as the client sees it."""

    run_id: str
    status: str
    label: str
    stages_done: int
    stages_total: int
    current_stage: str | None
    finished: bool
    steps: tuple[Step, ...] = field(default=())

    @property
    def text(self) -> str:
        """``3 of 7``."""
        return f"{self.stages_done} of {self.stages_total}"

    def as_json(self) -> dict[str, object]:
        """Client JSON (also what the ETag hashes)."""
        body = asdict(self)
        body["steps"] = [asdict(st) for st in self.steps]
        return {**body, "text": self.text}

    @property
    def etag(self) -> str:
        """A strong ETag of the client JSON."""
        body = json.dumps(self.as_json(), sort_keys=True, separators=(",", ":"))
        return '"' + hashlib.sha256(body.encode()).hexdigest()[:32] + '"'


def _states(status: str, reached: Sequence[bool]) -> list[str]:
    """Each step's state from whether it was reached (done) and the run's status."""
    finished = status in {s.value for s in TERMINAL}
    if status == "COMPLETED":
        return ["done"] * len(reached)
    out: list[str] = []
    first_open = True
    for done in reached:
        if done:
            out.append("done")
        elif first_open:
            first_open = False
            if finished:
                out.append("stopped")
            elif status == "AWAITING_APPROVAL":
                out.append("paused")
            else:
                out.append("running" if status in WORKING else "waiting")
        else:
            out.append("not_reached" if finished else "waiting")
    return out


def compute(
    run_id: str,
    status: str,
    stages: Sequence[tuple[str, str]],
    owners: Mapping[str, str],
    rows: Iterable[tuple[str, str | None]],
    *,
    nodes_done: Collection[str] | None = None,
) -> Progress:
    """Progress from the stage list (id, name), the row owners (row key -> stage id) and the
    run's coverage rows (row key, status). ``nodes_done``: the ABM graph's finished nodes, for a
    run on that graph (its steps are then ``ABM_STEPS``)."""
    rows = list(rows)
    if nodes_done is not None:
        return _abm(run_id, status, rows, nodes_done)
    finished = status in {s.value for s in TERMINAL}
    owned: dict[str, list[str | None]] = {sid: [] for sid, _ in stages}
    for key, row_status in rows:
        stage = owners.get(key)
        if stage in owned:
            owned[stage].append(row_status)
    reached: list[bool] = []
    for i, (sid, _) in enumerate(stages):
        statuses = owned[sid]
        if statuses:
            reached.append(all(s is not None for s in statuses))
        else:
            later = [st for later_id, _ in stages[i + 1 :] for st in owned[later_id]]
            reached.append(any(s is not None for s in later))
    done_flags = [True] * len(stages) if finished else reached
    current = next((name for (sid, name), d in zip(stages, done_flags, strict=True) if not d), None)
    states = _states(status, reached)
    steps = tuple(
        Step(
            sid,
            name,
            state,
            sum(1 for s in owned[sid] if s is not None),
            len(owned[sid]),
        )
        for (sid, name), state in zip(stages, states, strict=True)
    )
    return Progress(
        run_id=run_id,
        status=status,
        label=STATUS_LABELS.get(status, "Researching"),
        stages_done=sum(done_flags),
        stages_total=len(stages),
        current_stage=None if finished else current,
        finished=finished,
        steps=steps,
    )


def _abm(
    run_id: str, status: str, rows: Sequence[tuple[str, str | None]], nodes_done: Collection[str]
) -> Progress:
    """Progress of a run on the ABM graph: a step is done once the graph has finished a node of
    it and gone past it (a node it skipped, such as an approval nobody needed, is passed over)."""
    finished = status in {s.value for s in TERMINAL}
    order = [n for _, _, nodes in ABM_STEPS for n in nodes]
    seen = [i for i, n in enumerate(order) if n in nodes_done]
    last = max(seen, default=-1)
    reached: list[bool] = []
    position = 0
    for _, _, nodes in ABM_STEPS:
        end = position + len(nodes) - 1
        reached.append(last >= end)
        position = end + 1
    states = _states(status, reached)
    reported = sum(1 for _, s in rows if s is not None)
    steps = tuple(
        Step(sid, name, state, *((reported, len(rows)) if sid == "read" else (0, 0)))
        for (sid, name, _), state in zip(ABM_STEPS, states, strict=True)
    )
    current = next((st.name for st in steps if st.state != "done"), None)
    return Progress(
        run_id=run_id,
        status=status,
        label=STATUS_LABELS.get(status, "Researching"),
        stages_done=len(steps) if status == "COMPLETED" else sum(reached),
        stages_total=len(steps),
        current_stage=None if finished else current,
        finished=finished,
        steps=steps,
    )
