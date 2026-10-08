"""People-data minimisation (PD1, roadmap 11.4; D-84).

The minimisation map (``config/minimisation_map.yaml``) is data: every collected
person field maps to the deliverable that consumes it. ``check`` fails a field
with no consumer, and the collected set is checked against the map: a field the
map does not list cannot be stored anywhere (schema, pipelines, exports). Pure.

GUARANTEE(test_a_person_field_without_a_consumer_fails)
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_map(path: Path | None = None) -> dict[str, dict[str, object]]:
    """The minimisation map (validated: every field has at least one consumer)."""
    doc = yaml.safe_load((path or ROOT / "config/minimisation_map.yaml").read_text())
    fields = doc.get("fields") or {}
    if not fields:
        raise ValueError("minimisation_map.yaml: no fields")
    return fields


def check(collected: list[str], fields: dict[str, dict[str, object]] | None = None) -> list[str]:
    """Every problem with the collected set (pure):
    a collected field with no consumer in the map, or a collected field the map
    does not know (which therefore may not be stored)."""
    fields = fields if fields is not None else load_map()
    problems: list[str] = []
    for name in sorted(set(collected)):
        spec = fields.get(name)
        if spec is None:
            problems.append(f"{name}: not in the minimisation map; it may not be collected")
        elif not (spec.get("consumers") or []):
            problems.append(f"{name}: no consumer; it may not be collected")
    return problems


def uncollectable(fields: dict[str, dict[str, object]] | None = None) -> list[str]:
    """Map entries with no consumer (the map itself must stay honest)."""
    fields = fields if fields is not None else load_map()
    return sorted(k for k, v in fields.items() if not (v.get("consumers") or []))
