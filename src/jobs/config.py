"""Validated configuration loading and deterministic configuration hashing."""

import hashlib
from pathlib import Path
from typing import Any

import yaml

from jobs.errors import ConfigError


def load_yaml(path: Path) -> tuple[dict[str, Any], str]:
    """Load a YAML mapping and return it with a content hash."""
    try:
        raw = path.read_bytes()
        value = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load configuration {path.name}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"configuration {path.name} must contain a mapping")
    return value, hashlib.sha256(raw).hexdigest()
