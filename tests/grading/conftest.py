"""Shared fixtures for grading tests. All fixtures are synthetic."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from account_intelligence.grading.config import GradingConfig, load_grading_config

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / ".claude" / "skills" / "account-intelligence-platform"
NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(scope="session")
def cfg() -> GradingConfig:
    return load_grading_config()


def load_reference() -> ModuleType:
    """The skill's behavioural spec, imported by path (it is not a package)."""
    path = SKILL / "scripts" / "grading_reference.py"
    spec = importlib.util.spec_from_file_location("grading_reference", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["grading_reference"] = module
    spec.loader.exec_module(module)
    return module
