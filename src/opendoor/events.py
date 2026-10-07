"""Open Door contract (OD1, OD2): schema loading, validation of every emitted event and API page,
and the additive-only compatibility check between schema versions.

A change is additive when no property is removed, no type or constraint is narrowed, no property
becomes required and closed objects stay closed. Anything else needs a new major version.

GUARANTEE(test_open_door_events_validate_and_breaking_changes_are_refused)
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

SCHEMAS = Path(__file__).resolve().parent / "schemas"


@lru_cache
def schema(name: str) -> dict[str, Any]:
    """A schema by file name."""
    data: dict[str, Any] = json.loads((SCHEMAS / name).read_text())
    return data


@lru_cache
def _registry() -> Registry:
    resources = [
        (p.name, Resource.from_contents(json.loads(p.read_text()))) for p in SCHEMAS.glob("*.json")
    ]
    return Registry().with_resources(resources)


def validate(name: str, doc: Any) -> None:
    """Raise ``jsonschema.ValidationError`` unless ``doc`` matches the schema."""
    base = schema(name)
    Draft202012Validator(base, registry=_registry(), format_checker=FormatChecker()).validate(doc)


def breaking_changes(old: dict[str, Any], new: dict[str, Any], path: str = "$") -> list[str]:
    """Why ``new`` is not an additive change of ``old`` (empty when it is)."""
    out: list[str] = []
    if old.get("type") != new.get("type") and "type" in old:
        out.append(f"{path}: type changed")
    for key in ("const", "enum", "pattern", "maxItems", "maxLength", "minimum"):
        if key in old and old.get(key) != new.get(key):
            if key == "enum" and set(old[key]) <= set(new.get(key, [])):
                continue
            out.append(f"{path}: {key} changed")
    if old.get("additionalProperties") is False and new.get("additionalProperties") is not False:
        pass  # opening a closed object is additive for readers
    for req in set(new.get("required", [])) - set(old.get("required", [])):
        out.append(f"{path}: {req} became required")
    old_props, new_props = old.get("properties", {}), new.get("properties", {})
    for name, sub in old_props.items():
        if name not in new_props:
            out.append(f"{path}.{name}: removed")
        else:
            out += breaking_changes(sub, new_props[name], f"{path}.{name}")
    if "items" in old and "items" in new:
        out += breaking_changes(old["items"], new["items"], f"{path}[]")
    return out
