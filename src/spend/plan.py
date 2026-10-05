"""``PlanPort`` from the workflow specification (proposal, D-29).

For every research stage after setup: the stage's allowed operations (tool categories mapped to
operations by the grading configuration), restricted to operations the provider table supports,
share the stage's ``loop_cap`` calls (at least one call each). The plan item serves the stage's
highest-priority coverage row (``assets/checklist.yaml`` priority) and is mandatory when that row
is High. The people stage asks only for the active people source's operations (P0-3). This is an
estimate of what the stages may call, not a promise that they will.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from account_intelligence.tracker.config import load_checklist
from account_intelligence.workflow.spec import WorkflowSpec
from spend.models import OpCost, PlanItem
from spend.priority import PRIORITY_RANK

OPERATION_PROVIDER: dict[str, str] = {}


def _provider_of(operation: str, costs: dict[tuple[str, str], OpCost]) -> str | None:
    matches = sorted(code for code, op in costs if op == operation)
    return matches[0] if matches else None


def spec_plan(
    spec: WorkflowSpec,
    costs: dict[tuple[str, str], OpCost],
    category_by_operation: dict[str, str],
    people_operations: Iterable[str],
) -> list[PlanItem]:
    """Plan items for every stage after setup (see the module docstring)."""
    checklist = load_checklist()
    people = set(people_operations)
    items: list[PlanItem] = []
    for stage in spec.stages:
        if stage.id == "setup" or not stage.coverage or not stage.allowed_tools:
            continue
        ops = sorted(op for op, cat in category_by_operation.items() if cat in stage.allowed_tools)
        if stage.id == "people":
            ops = [op for op in ops if op in people]
        located = [(op, _provider_of(op, costs)) for op in ops]
        usable = [(op, code) for op, code in located if code is not None]
        if not usable:
            continue
        rows = [checklist.row(key) for key in stage.coverage]
        known = [r for r in rows if r is not None]
        top = min(known, key=lambda r: PRIORITY_RANK.get(r.priority, 3)) if known else None
        row_key = top.key if top else stage.coverage[0]
        priority = top.priority if top and top.priority in PRIORITY_RANK else "Optional"
        calls = max(stage.loop_cap, 1)
        share = max(-(-calls // len(usable)), 1)
        for op, code in usable:
            items.append(
                PlanItem(
                    row_key=row_key,
                    stage=stage.id,
                    priority=priority,
                    provider_code=code,
                    operation=op,
                    calls=share,
                    mandatory=priority == "High",
                )
            )
    return items


def stage_order(spec: WorkflowSpec) -> Sequence[str]:
    """Stage ids in execution order."""
    return tuple(spec.topological_order)
