"""Fixtures for app/ tests. Everything here is offline: no broker, model or network calls."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.golden.loader import DEFAULT_GOLDEN_DIR
from app.rules import RuleBundle, load_rules
from app.rules.loader import DEFAULT_CONFIG_DIR

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def rules() -> RuleBundle:
    return load_rules()


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    """A writable copy of config/ for mutation tests."""
    dst = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR, dst)
    return dst


EditYaml = Callable[[str, Callable[[dict[str, Any]], None]], None]


@pytest.fixture()
def edit_yaml(config_dir: Path) -> EditYaml:
    def _edit(name: str, change: Callable[[dict[str, Any]], None]) -> None:
        path = config_dir / name
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    return _edit


@pytest.fixture()
def golden_dir(tmp_path: Path) -> Path:
    """A writable copy of the golden accounts."""
    dst = tmp_path / "golden"
    shutil.copytree(DEFAULT_GOLDEN_DIR, dst)
    return dst
