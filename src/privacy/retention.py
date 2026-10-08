"""Retention (PD2, roadmap 11.4; D-83): the sourced windows, a dry run first.

``select`` is the pure oracle: which rows (by class, age and legal hold) the
windows say to remove. The job shows exactly what it would remove (counts and
ids), and only a second, audited step executes; deletion writes tombstones
(``privacy.tombstones``). Ledgers and the audit log are exempt by their own
windows in ``config/retention.yaml``. Bounded batches, a cursor, idempotent: a
crash mid-run resumes without double deletion, because an action recorded for
(row, run) is never repeated and the row is simply gone.

GUARANTEE(test_retention_removes_exactly_the_dry_run)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml

ROOT = Path(__file__).resolve().parents[2]
BATCH = 500


@dataclass(frozen=True)
class Row:
    """One candidate row as the selector sees it."""

    row_id: str
    cls: str
    age_days: int
    held: bool = False


@dataclass(frozen=True)
class Plan:
    """What a retention run would do, per class."""

    remove: dict[str, tuple[str, ...]]
    keep: dict[str, tuple[str, ...]]  # held rows and classes with no window


def windows(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """The sourced windows (validated: every class names an action)."""
    doc = yaml.safe_load((path or ROOT / "config/retention.yaml").read_text())
    classes = doc.get("classes") or {}
    if not classes:
        raise ValueError("retention.yaml: no classes")
    return classes


def _limit(spec: dict[str, Any]) -> int | None:
    if spec.get("permanent"):
        return None
    if "days" in spec:
        return int(spec["days"])
    if "days_hot" in spec:  # hot window is what deletes
        return int(spec["days_hot"])
    return None


def select(rows: list[Row], cfg: dict[str, Any] | None = None) -> Plan:
    """Exactly which rows the windows remove, and which are kept (pure oracle)."""
    cfg = cfg or windows()
    remove: dict[str, list[str]] = {}
    keep: dict[str, list[str]] = {}
    for row in sorted(rows, key=lambda r: (r.cls, r.row_id)):
        spec = cfg.get(row.cls)
        limit = _limit(spec) if spec else None
        if spec is None or limit is None or row.held or row.age_days < limit:
            keep.setdefault(row.cls, []).append(row.row_id)
        else:
            remove.setdefault(row.cls, []).append(row.row_id)
    return Plan(
        remove={k: tuple(v) for k, v in remove.items()},
        keep={k: tuple(v) for k, v in keep.items()},
    )


def dry_run_report(plan: Plan, at: datetime) -> str:
    """The operator-facing dry run: exact counts and ids, nothing executed."""
    lines = [f"retention dry run at {at.isoformat()}"]
    if not plan.remove:
        lines.append("nothing to remove")
    for cls in sorted(plan.remove):
        ids = plan.remove[cls]
        lines.append(f"{cls}: {len(ids)} to remove: {', '.join(ids)}")
    for cls in sorted(plan.keep):
        lines.append(f"{cls}: {len(plan.keep[cls])} kept (window, hold or permanent)")
    return "\n".join(lines)


Mode = Literal["dry_run", "execute"]


def batches(plan: Plan, cursor: int = 0, size: int = BATCH) -> list[tuple[str, str]]:
    """The flat, ordered work list after the cursor (resumable; pure)."""
    flat = [(cls, rid) for cls in sorted(plan.remove) for rid in plan.remove[cls]]
    return flat[cursor:][:size]


def age_days(*, at: datetime, since: datetime) -> int:
    """Whole days between two moments (pure; the selector's clock comes in, never `now`)."""
    return (at - since).days


def deadline(cls: str, at: datetime, cfg: dict[str, Any] | None = None) -> datetime | None:
    """When a row of this class becomes removable (pure)."""
    spec = (cfg or windows()).get(cls)
    limit = _limit(spec) if spec else None
    return None if limit is None else at + timedelta(days=limit)
