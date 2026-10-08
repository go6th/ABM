"""What a run pins (DM2, roadmap 11.1: "the config version pins the checklist used").

``current`` computes the checklist version (sha256 of ``config/checklist.yaml``) and the model
versions (profile -> model id from ``config/models.yaml``). ``runs.lifecycle.create_run`` stores
both with the graph version at creation; the database trigger ``account_runs_guard_pins`` makes
every pin immutable (set once). ``of_run`` reads a run's pins, which is what a resume, retry or
re-run must use: never the current files.

GUARANTEE(test_a_run_stays_pinned_when_config_changes_mid_run)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import text

from account_intelligence.config import CONFIG_DIR


@dataclass(frozen=True)
class Pins:
    """A run's pinned versions."""

    graph_version: str | None
    checklist_version: str | None
    model_versions: dict[str, str] | None
    grading_config_hash: str | None
    settings_version: int | None


def checklist_version(path: Path | None = None) -> str:
    """sha256 of the checklist configuration."""
    p = path or CONFIG_DIR / "checklist.yaml"
    return hashlib.sha256(p.read_bytes()).hexdigest()


def model_versions(path: Path | None = None) -> dict[str, str]:
    """Profile -> model id."""
    doc = yaml.safe_load((path or CONFIG_DIR / "models.yaml").read_text())
    return {str(k): str(v["model"]) for k, v in sorted(doc["profiles"].items())}


async def of_run(s: Any, run_id: UUID) -> Pins:
    """A run's pins."""
    r = (
        await s.execute(
            text(
                "SELECT graph_version, checklist_version, model_versions, grading_config_hash,"
                " settings_version FROM account_runs WHERE id = :r"
            ),
            {"r": run_id},
        )
    ).one()
    return Pins(
        r.graph_version,
        r.checklist_version,
        r.model_versions,
        r.grading_config_hash,
        r.settings_version,
    )
