"""Schema fingerprints for provider responses (CX1, roadmap 11.2; D-88).

A fingerprint is the sha256 of a response's *shape*: the type tree over keys and
types, never values (values would pin content, not contract). Recorded from
fixtures; every live response is checked passively against the stored shape
(``contracts.drift``). Pure module: no database, clock or network.

GUARANTEE(test_breaking_drift_disables_the_operation_fail_closed)
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

Shape = dict[str, Any]


def shape_of(value: Any) -> Shape:
    """The structural shape of one decoded JSON value (pure, deterministic)."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        # arrays are described by their first element; an empty array is open
        items = shape_of(value[0]) if value else None
        return {"type": "array", "items": items}
    if isinstance(value, dict):
        return {
            "type": "object",
            "fields": {str(k): shape_of(v) for k, v in sorted(value.items())},
        }
    raise TypeError(f"unsupported payload type: {type(value).__name__}")


def canonical(shape: Shape) -> str:
    """The deterministic serialisation of a shape (pure)."""
    return json.dumps(shape, sort_keys=True, separators=(",", ":"))


def fingerprint(payload: Any) -> str:
    """sha256 of the canonical shape of a response (pure)."""
    return hashlib.sha256(canonical(shape_of(payload)).encode()).hexdigest()


def enum_values(payload: Any, path: str) -> frozenset[str]:
    """The distinct string values at a dotted path (``a.b`` or ``items.x`` meaning
    every element of an array on the way). Pure; used for enum drift rules."""
    out: set[str] = set()

    def walk(node: Any, parts: list[str]) -> None:
        if not parts:
            if isinstance(node, str):
                out.add(node)
            return
        head, rest = parts[0], parts[1:]
        if isinstance(node, list):
            for item in node:
                walk(item, parts)
        elif isinstance(node, dict) and head in node:
            walk(node[head], rest)

    walk(payload, path.split("."))
    return frozenset(out)
