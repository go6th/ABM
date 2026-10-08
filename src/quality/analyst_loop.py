"""The analyst loop (QS3, roadmap 11.3): a quarterly labelling task on a fresh
sample; disagreements become eval cases.

The sample is drawn by a seeded, logged rule (the seed and the exclusion list
are stored with the task, so the draw is reproducible) and excludes items
already used by earlier tasks. Analyst-versus-verifier disagreements become new
eval cases carrying their provenance. Labels are insert-only. The quarterly
schedule and its overdue state are visible to operators.

GUARANTEE(test_the_analyst_loop_samples_fresh_and_makes_cases)
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text


@dataclass(frozen=True)
class EvalCase:
    """A new eval case built from a disagreement, with its provenance."""

    item_ref: str
    analyst_label: str
    verifier_label: str
    provenance: str


@dataclass(frozen=True)
class Task:
    """A quarterly labelling task as stored."""

    id: int
    quarter: str
    seed: int
    items: tuple[str, ...]
    due_at: datetime
    status: str


def draw_sample(
    pool: list[str], k: int, seed: int, exclude: frozenset[str] | set[str] = frozenset()
) -> tuple[str, ...]:
    """A fresh, reproducible sample: seeded shuffle, used items excluded (pure)."""
    if k < 1:
        raise ValueError("k must be at least 1")
    fresh = [item for item in pool if item not in exclude]
    rng = random.Random(seed)
    rng.shuffle(fresh)
    return tuple(fresh[:k])


def make_cases(disagreements: list[tuple[str, str, str, str]]) -> tuple[EvalCase, ...]:
    """Analyst-versus-verifier disagreements become eval cases with provenance."""
    return tuple(
        EvalCase(item_ref=i, analyst_label=a, verifier_label=v, provenance=p)
        for i, a, v, p in disagreements
    )


def is_overdue(task: Task, now: datetime) -> bool:
    """A task nobody completed by its due date is overdue (pure)."""
    return task.status == "open" and now > task.due_at


async def create_task(
    s: Any,
    *,
    quarter: str,
    pool: list[str],
    k: int,
    seed: int,
    exclude: frozenset[str] | set[str],
    due_at: datetime,
) -> Task:
    """Create one quarterly task; the sample, seed and exclusions are stored so
    the draw is reproducible and auditable."""
    items = list(draw_sample(pool, k, seed, exclude))
    row = (
        await s.execute(
            text(
                "INSERT INTO analyst_label_tasks (quarter, seed, excluded, items, due_at, status)"
                " VALUES (:q, :seed, CAST(:ex AS jsonb), CAST(:it AS jsonb), :due, 'open')"
                " RETURNING id"
            ),
            {
                "q": quarter,
                "seed": seed,
                "ex": json.dumps(sorted(exclude)),
                "it": json.dumps(items),
                "due": due_at,
            },
        )
    ).scalar_one()
    return Task(row, quarter, seed, tuple(items), due_at, "open")


async def open_items(s: Any) -> list[str]:
    """Items already used by any earlier task (never drawn twice)."""
    rows = (
        await s.execute(text("SELECT items FROM analyst_label_tasks ORDER BY id"))
    ).fetchall()
    out: set[str] = set()
    for (blob,) in rows:
        out |= set(blob if isinstance(blob, list) else json.loads(blob))
    return sorted(out)


async def record_label(
    s: Any, *, task_id: int, item_ref: str, label: str, analyst: str, note: str = ""
) -> None:
    """One analyst label (insert-only)."""
    if not label.strip() or not analyst.strip():
        raise ValueError("label and analyst are required")
    await s.execute(
        text(
            "INSERT INTO analyst_labels (task_id, item_ref, label, analyst, note)"
            " VALUES (:t, :i, :l, :a, :n)"
        ),
        {"t": task_id, "i": item_ref, "l": label, "a": analyst, "n": note},
    )


async def tasks(s: Any) -> list[Task]:
    """Every task, oldest first (the operator's overdue view)."""
    rows = (
        await s.execute(
            text(
                "SELECT id, quarter, seed, items, due_at, status FROM analyst_label_tasks"
                " ORDER BY id"
            )
        )
    ).fetchall()
    return [
        Task(
            r.id,
            r.quarter,
            r.seed,
            tuple(r.items if isinstance(r.items, list) else json.loads(r.items)),
            r.due_at,
            r.status,
        )
        for r in rows
    ]
