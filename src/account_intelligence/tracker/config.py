"""Load and validate the tracker configuration and the coverage checklist (fail fast).

Two files: ``config/tracker_v1.yaml`` (the D-12 completion policy and the D-13 transition and
derivation rules, all ``status: proposal``) and ``config/checklist.yaml`` (the 33 rows). The
checklist keys are the canonical keys of the skill's ``assets/checklist.yaml``;
``tests/workflow/test_stage_map.py`` proves the two agree. Both loaders are cached per path and
return frozen models, so there is no mutable module state. Each result carries the SHA-256 of
the file bytes, which seeding pins in ``run_coverage_meta`` so a later edit cannot change an
existing run.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from account_intelligence.config import CONFIG_DIR
from account_intelligence.tracker.errors import ConfigError

STATUSES = ("done", "partial", "not_done", "blocked", "none")
PRIORITIES = ("high", "medium", "optional")


class ChecklistRow(BaseModel):
    """One coverage row as configured (key, pass, priority, label)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    pass_no: int
    priority: str
    label: str
    lane: str | None = None

    @field_validator("priority")
    @classmethod
    def _priority(cls, value: str) -> str:
        if value not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}")
        return value


class ChecklistConfig(BaseModel):
    """The validated checklist and its content hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: str
    sha256: str
    rows: tuple[ChecklistRow, ...]

    def row(self, key: str) -> ChecklistRow | None:
        """Return the configured row for ``key`` or ``None``."""
        return next((r for r in self.rows if r.key == key), None)


class TrackerConfig(BaseModel):
    """``config/tracker_v1.yaml``: proposals D-12 and D-13 (never presented as settled)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: str
    status: str
    sha256: str
    d_12_completion_policy: dict[str, Any]
    d_13_tracker_rules: dict[str, Any]
    reason_codes: dict[str, str]
    transitions: dict[str, dict[str, list[str]]]
    # section 11 consistency rules (tracker/consistency.py); empty: no rule is applied
    consistency: dict[str, Any] = {}

    @property
    def attempts_per_row(self) -> int:
        """Per-row attempt cap: 1 + the planner gap loop cap (2) = 3."""
        return int(self.d_13_tracker_rules["attempts_per_row"])

    @property
    def out_of_scope_reasons(self) -> frozenset[str]:
        """Reason codes that may justify a None via scope (T4b)."""
        return frozenset(self.d_13_tracker_rules["out_of_scope_reasons"])

    @property
    def error_severity_order(self) -> tuple[str, ...]:
        """Connector error classes, most severe first."""
        return tuple(self.d_13_tracker_rules["error_severity_order"])

    @property
    def error_reason_map(self) -> dict[str, str]:
        """Connector error class to tracker reason code."""
        return dict(self.d_13_tracker_rules["error_reason_map"])

    @property
    def verifier_rows(self) -> frozenset[str]:
        """Rows the verifier actor may report (T7)."""
        return frozenset(self.d_13_tracker_rules["verifier_rows"])

    @property
    def detail_max_chars(self) -> int:
        """Length cap for the internal-only detail field (T9)."""
        return int(self.d_13_tracker_rules["detail_max_chars"])


def _read(path: Path) -> tuple[bytes, Any]:
    try:
        raw = path.read_bytes()
        return raw, yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"cannot load {path.name}: {error}") from error


@lru_cache(maxsize=4)
def load_tracker_config(path: Path | None = None) -> TrackerConfig:
    """Load and validate the tracker rules; every reason code used in config must exist."""
    raw, data = _read(path or CONFIG_DIR / "tracker_v1.yaml")
    try:
        cfg = TrackerConfig.model_validate({**data, "sha256": hashlib.sha256(raw).hexdigest()})
    except ValidationError as error:
        raise ConfigError(f"invalid tracker config: {error}") from error
    from account_intelligence.tracker.model import ReasonCode  # avoid an import cycle

    known = {code.value for code in ReasonCode}
    used = set(cfg.reason_codes) | cfg.out_of_scope_reasons | set(cfg.error_reason_map.values())
    if not used <= known or set(cfg.reason_codes) != known:
        raise ConfigError(f"reason codes differ from the ReasonCode enum: {sorted(used ^ known)}")
    for source, targets in cfg.transitions.items():
        if source not in (*STATUSES, "unset") or not set(targets) <= set(STATUSES):
            raise ConfigError(f"transition table has an unknown status under {source}")
    return cfg


@lru_cache(maxsize=4)
def load_checklist(path: Path | None = None) -> ChecklistConfig:
    """Load the 33 rows; ``key`` is canonical and priorities are normalised to lower case."""
    raw, data = _read(path or CONFIG_DIR / "checklist.yaml")
    try:
        rows = tuple(
            ChecklistRow(
                key=str(r["key"]),
                pass_no=int(r["pass"]),
                priority=str(r["priority"]).lower(),
                label=str(r.get("row", r.get("label", ""))),
                lane=r.get("lane"),
            )
            for r in data["rows"]
        )
    except (KeyError, TypeError, ValidationError) as error:
        raise ConfigError(f"invalid checklist: {error}") from error
    if len({r.key for r in rows}) != len(rows):
        raise ConfigError("checklist keys must be unique")
    return ChecklistConfig(
        version=str(data["version"]), sha256=hashlib.sha256(raw).hexdigest(), rows=rows
    )


def load_checklist_config() -> tuple[ChecklistConfig, str]:
    """Compatibility accessor: the checklist and its hash."""
    cfg = load_checklist()
    return cfg, cfg.sha256
