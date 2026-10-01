"""Load and validate the rule config bundle, and compute the version a run pins."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from app.rules.schemas import (
    Checklist,
    Grading,
    Lanes,
    Models,
    OutputSpec,
    PeopleScope,
    Routing,
)

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

CONFIG_FILES = (
    "checklist.yaml",
    "grading.yaml",
    "routing.yaml",
    "output_spec.yaml",
    "models.yaml",
    "people_scope.yaml",
    "lanes.yaml",
)


class ConfigError(ValueError):
    """Raised when a config file is missing, unparsable or fails validation."""


@dataclass(frozen=True)
class RuleBundle:
    checklist: Checklist
    grading: Grading
    routing: Routing
    output_spec: OutputSpec
    models: Models
    people_scope: PeopleScope
    lanes: Lanes
    file_versions: dict[str, str]
    content_hash: str

    @property
    def version(self) -> str:
        """Bundle version a run pins, e.g. ``cl1.0.0-gr1.0.0-...+3f9a1c2b7d4e``.

        Declared versions say what an editor meant to release; the content hash catches an
        edit that forgot to bump a version.
        """
        parts = "-".join(
            f"{_abbrev(name)}{ver}" for name, ver in sorted(self.file_versions.items())
        )
        return f"{parts}+{self.content_hash[:12]}"


def _abbrev(filename: str) -> str:
    stem = filename.removesuffix(".yaml")
    return "".join(word[:2] for word in stem.split("_"))


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"missing config file: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: top level must be a mapping")
    return data


def _load[T: BaseModel](root: Path, name: str, schema: type[T], digest: Any) -> T:
    path = root / name
    data = _read_yaml(path)
    try:
        model = schema.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{name}: {exc}") from exc
    digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    return model


def load_rules(config_dir: Path | str = DEFAULT_CONFIG_DIR) -> RuleBundle:
    """Load every config file, validate it and return the pinned bundle."""
    root = Path(config_dir)
    digest = hashlib.sha256()
    bundle = RuleBundle(
        checklist=_load(root, "checklist.yaml", Checklist, digest),
        grading=_load(root, "grading.yaml", Grading, digest),
        routing=_load(root, "routing.yaml", Routing, digest),
        output_spec=_load(root, "output_spec.yaml", OutputSpec, digest),
        models=_load(root, "models.yaml", Models, digest),
        people_scope=_load(root, "people_scope.yaml", PeopleScope, digest),
        lanes=_load(root, "lanes.yaml", Lanes, digest),
        file_versions={},
        content_hash="",
    )
    versions = {
        "checklist.yaml": bundle.checklist.version,
        "grading.yaml": bundle.grading.version,
        "routing.yaml": bundle.routing.version,
        "output_spec.yaml": bundle.output_spec.version,
        "models.yaml": bundle.models.version,
        "people_scope.yaml": bundle.people_scope.version,
        "lanes.yaml": bundle.lanes.version,
    }
    return replace(bundle, file_versions=versions, content_hash=digest.hexdigest())


@lru_cache(maxsize=1)
def default_rules() -> RuleBundle:
    """The bundle from the repository config directory, loaded once per process."""
    return load_rules()
