"""Run progress for the client (VW5): stages done of stages, never an ETA, a provider, a credit
count, a cost, a call reference or another account. Pure.

A research stage owns coverage rows (``config/workflow_v1.yaml``); it is done when every row it
owns has a status (done, partial, not done, blocked or none: rule 10). A stage that owns no rows
(setup) is done once any later stage has settled a row, or when the run has ended. A finished run
shows every stage done whatever the rows say (a cancelled or failed run says so in its status).

GUARANTEE(test_progress_shows_stage_counts_only)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass

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

    @property
    def text(self) -> str:
        """``3 of 7``."""
        return f"{self.stages_done} of {self.stages_total}"

    def as_json(self) -> dict[str, object]:
        """Client JSON (also what the ETag hashes)."""
        return {**asdict(self), "text": self.text}

    @property
    def etag(self) -> str:
        """A strong ETag of the client JSON."""
        body = json.dumps(self.as_json(), sort_keys=True, separators=(",", ":"))
        return '"' + hashlib.sha256(body.encode()).hexdigest()[:32] + '"'


def compute(
    run_id: str,
    status: str,
    stages: Sequence[tuple[str, str]],
    owners: Mapping[str, str],
    rows: Iterable[tuple[str, str | None]],
) -> Progress:
    """Progress from the stage list (id, name), the row owners (row key -> stage id) and the
    run's coverage rows (row key, status)."""
    finished = status in {s.value for s in TERMINAL}
    owned: dict[str, list[str | None]] = {sid: [] for sid, _ in stages}
    for key, row_status in rows:
        stage = owners.get(key)
        if stage in owned:
            owned[stage].append(row_status)
    done_flags: list[bool] = []
    for i, (sid, _) in enumerate(stages):
        statuses = owned[sid]
        if finished:
            done_flags.append(True)
        elif statuses:
            done_flags.append(all(s is not None for s in statuses))
        else:
            later = [st for later_id, _ in stages[i + 1 :] for st in owned[later_id]]
            done_flags.append(any(s is not None for s in later))
    current = next((name for (sid, name), d in zip(stages, done_flags, strict=True) if not d), None)
    return Progress(
        run_id=run_id,
        status=status,
        label=STATUS_LABELS.get(status, "Researching"),
        stages_done=sum(done_flags),
        stages_total=len(stages),
        current_stage=None if finished else current,
        finished=finished,
    )
