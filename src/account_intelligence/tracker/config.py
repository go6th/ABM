"""Tracker config loading and validation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from account_intelligence.tracker.errors import ConfigError

CONFIG_DIR = Path("config")
ASSETS_DIR = Path(".claude/skills/account-intelligence-platform/assets")
if not ASSETS_DIR.exists():
    ASSETS_DIR = Path("assets")


class TrackerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    version: str
    status: str
    d_12_completion_policy: dict[str, Any]
    d_13_tracker_rules: dict[str, Any]
    reason_codes: dict[str, str]
    transitions: dict[str, Any]


class ChecklistConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    version: str
    statuses: list[str]
    priorities: list[str]
    rows: list[dict[str, Any]]


_TRACKER_CONFIG: TrackerConfig | None = None
_CHECKLIST_CONFIG: ChecklistConfig | None = None
_CHECKLIST_HASH: str = ""


def load_tracker_config() -> TrackerConfig:
    global _TRACKER_CONFIG
    if _TRACKER_CONFIG is not None:
        return _TRACKER_CONFIG
    try:
        with open(CONFIG_DIR / "tracker_v1.yaml") as f:
            data = yaml.safe_load(f)
        _TRACKER_CONFIG = TrackerConfig.model_validate(data)
        return _TRACKER_CONFIG
    except Exception as e:
        raise ConfigError(f"Failed to load tracker config: {e}")


def load_checklist_config() -> tuple[ChecklistConfig, str]:
    global _CHECKLIST_CONFIG, _CHECKLIST_HASH
    if _CHECKLIST_CONFIG is not None:
        return _CHECKLIST_CONFIG, _CHECKLIST_HASH
    try:
        with open(ASSETS_DIR / "checklist.yaml", "rb") as f:
            raw = f.read()
            _CHECKLIST_HASH = hashlib.sha256(raw).hexdigest()
            data = yaml.safe_load(raw)
        _CHECKLIST_CONFIG = ChecklistConfig.model_validate(data)
        return _CHECKLIST_CONFIG, _CHECKLIST_HASH
    except Exception as e:
        raise ConfigError(f"Failed to load checklist config: {e}")
