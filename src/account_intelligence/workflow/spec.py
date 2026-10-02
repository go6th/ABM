"""Load and validate the declarative workflow DAG."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR


@dataclass(frozen=True)
class StageSpec:
    id: str
    name: str
    depends_on: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    budget_share: float
    loop_cap: int
    coverage: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowSpec:
    version: str
    stages: tuple[StageSpec, ...]
    coverage_owners: Mapping[str, str]
    coverage_rows: frozenset[str]
    topological_order: tuple[str, ...]
    settings: Mapping[str, Any]


def _topological(stages: tuple[StageSpec, ...]) -> tuple[str, ...]:
    by_id = {stage.id: stage for stage in stages}
    if len(by_id) != len(stages):
        raise ValueError("stage ids must be unique")
    for stage in stages:
        missing = set(stage.depends_on) - by_id.keys()
        if missing:
            raise ValueError(f"stage {stage.id} depends on unknown stages: {sorted(missing)}")
        if stage.id in stage.depends_on:
            raise ValueError(f"stage {stage.id} cannot depend on itself")
        if not 0 <= stage.budget_share <= 1:
            raise ValueError(f"stage {stage.id} budget_share must be between 0 and 1")
        if stage.loop_cap < 0:
            raise ValueError(f"stage {stage.id} loop_cap cannot be negative")
    incoming = {stage.id: set(stage.depends_on) for stage in stages}
    result: list[str] = []
    while incoming:
        ready = sorted(key for key, dependencies in incoming.items() if not dependencies)
        if not ready:
            raise ValueError("workflow dependency graph contains a cycle")
        for key in ready:
            result.append(key)
            del incoming[key]
        for dependencies in incoming.values():
            dependencies.difference_update(ready)
    return tuple(result)


def _coverage_owners(stages: tuple[StageSpec, ...], rows: frozenset[str]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for stage in stages:
        for row in stage.coverage:
            if row in owners:
                raise ValueError(f"coverage row {row} has multiple owners")
            owners[row] = stage.id
    unknown = owners.keys() - rows
    missing_rows = rows - owners.keys()
    if unknown or missing_rows:
        raise ValueError(
            "coverage ownership mismatch; "
            f"unknown={sorted(unknown)}, missing={sorted(missing_rows)}"
        )
    return owners


def load_spec(path: Path | None = None, checklist_path: Path | None = None) -> WorkflowSpec:
    """Read workflow configuration and cross-check it against the canonical checklist."""
    raw: dict[str, Any] = yaml.safe_load((path or CONFIG_DIR / "workflow_v1.yaml").read_text())
    checklist: dict[str, Any] = yaml.safe_load(
        (checklist_path or CONFIG_DIR / "checklist.yaml").read_text()
    )
    rows = frozenset(str(row.get("key", row.get("id"))) for row in checklist["rows"])
    parsed = tuple(
        StageSpec(
            id=str(item["id"]),
            name=str(item["name"]),
            depends_on=tuple(str(value) for value in item.get("depends_on", [])),
            allowed_tools=tuple(str(value) for value in item.get("allowed_tools", [])),
            budget_share=float(item.get("budget_share", 0)),
            loop_cap=int(item.get("loop_cap", 0)),
            coverage=tuple(str(value) for value in item.get("coverage", [])),
        )
        for item in raw["stages"]
    )
    owners = _coverage_owners(parsed, rows)
    return WorkflowSpec(
        version=str(raw["version"]),
        stages=parsed,
        coverage_owners=owners,
        coverage_rows=rows,
        topological_order=_topological(parsed),
        settings=raw.get("settings", {}),
    )
