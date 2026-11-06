"""Contract drift classification (CX2, roadmap 11.2; D-88).

Two shape trees in, one verdict out: ``same``, ``additive`` (new fields or new
enum values only) or ``breaking`` (a removed or renamed field, a type change, a
removed enum value, a nested shape change). Which changes are additive is
configuration (``config/drift.yaml``), never code opinion. Pure module.

GUARANTEE(test_breaking_drift_disables_the_operation_fail_closed)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Severity = Literal["same", "additive", "breaking"]


@dataclass(frozen=True)
class Change:
    """One difference between two shapes."""

    path: str
    kind: str  # field_added | field_removed | type_changed | enum_added | enum_removed
    detail: str


@dataclass(frozen=True)
class Verdict:
    """The classification of a response against a stored shape."""

    severity: Severity
    changes: tuple[Change, ...]


def _walk(old: dict[str, Any], new: dict[str, Any], path: str, out: list[Change]) -> None:
    if old.get("type") != new.get("type"):
        out.append(Change(path or "$", "type_changed", f"{old.get('type')} -> {new.get('type')}"))
        return
    if old.get("type") == "object":
        of, nf = old.get("fields", {}), new.get("fields", {})
        for k in sorted(nf.keys() - of.keys()):
            out.append(Change(f"{path}.{k}" if path else k, "field_added", "new field"))
        for k in sorted(of.keys() - nf.keys()):
            out.append(Change(f"{path}.{k}" if path else k, "field_removed", "field gone"))
        for k in sorted(of.keys() & nf.keys()):
            _walk(of[k], nf[k], f"{path}.{k}" if path else k, out)
    elif old.get("type") == "array" and old.get("items") and new.get("items"):
        _walk(old["items"], new["items"], f"{path}[]", out)


def shape_diff(old: dict[str, Any], new: dict[str, Any]) -> tuple[Change, ...]:
    """Every structural difference between two shape trees (pure)."""
    out: list[Change] = []
    _walk(old, new, "", out)
    return tuple(out)


def enum_diff(
    old_enums: dict[str, frozenset[str]], new_enums: dict[str, frozenset[str]]
) -> tuple[Change, ...]:
    """Enum value sets per configured path: a new value is additive, a missing
    value is breaking (consumers switch on it)."""
    out: list[Change] = []
    for path in sorted(set(old_enums) | set(new_enums)):
        was, now = old_enums.get(path, frozenset()), new_enums.get(path, frozenset())
        for v in sorted(now - was):
            out.append(Change(path, "enum_added", f"new value {v!r}"))
        for v in sorted(was - now):
            out.append(Change(path, "enum_removed", f"value {v!r} gone"))
    return tuple(out)


def classify(
    old_shape: dict[str, Any],
    new_shape: dict[str, Any],
    *,
    old_enums: dict[str, frozenset[str]] | None = None,
    new_enums: dict[str, frozenset[str]] | None = None,
    breaking_kinds: frozenset[str] = frozenset({"field_removed", "type_changed", "enum_removed"}),
) -> Verdict:
    """Same, additive or breaking (pure). ``breaking_kinds`` comes from
    ``config/drift.yaml``; the defaults above are the conservative set."""
    changes = shape_diff(old_shape, new_shape)
    if old_enums or new_enums:
        changes += enum_diff(old_enums or {}, new_enums or {})
    severity: Severity = "same"
    for c in changes:
        if c.kind in breaking_kinds:
            return Verdict("breaking", changes)
        if c.kind in ("field_added", "enum_added"):
            severity = "additive"
    return Verdict(severity, changes)
