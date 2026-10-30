"""The parity package's YAML reader: ``config/parity/texts.yaml`` (the bundle's fixed sentences)
and ``config/parity/platforms.yaml`` (the platform dictionary), read through ``CONFIG_DIR`` so an
image that installs the package non-editably finds them. The one place under ``src/parity`` that
loads YAML (``tests/guardrails/test_config_readers.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR

TEXTS_PATH = CONFIG_DIR / "parity" / "texts.yaml"
PLATFORMS_PATH = CONFIG_DIR / "parity" / "platforms.yaml"


def read_yaml(path: Path) -> dict[str, Any]:
    """The mapping a parity config file holds (empty for an empty file)."""
    with open(path, encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}
    return data
