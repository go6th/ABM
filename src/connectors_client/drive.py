"""Drive scope in code (CN2): every file is checked against the admin's folder allow-list by
RESOLVED ancestry, never by trusting a query. Pure (metadata comes in as a lookup).

* A file is allowed when it IS an allowed folder or one of its parent chains reaches an allowed
  folder within ``max_depth`` steps.
* Multiple parents: every chain is resolved; an unresolvable chain (a parent the lookup cannot
  see) makes the decision ``unknown_ancestry`` (deny), even if another chain is allowed.
* Shortcuts: the TARGET is what would be read, so the target's ancestry decides; a shortcut in an
  allowed folder pointing outside is ``shortcut_outside`` (deny).
* Shared drives: a file in a shared drive is allowed only through the same ancestry rule (the
  drive root or a folder inside it must be on the list).

GUARANTEE(test_drive_ancestry_denies_everything_outside_the_named_folders)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from connectors_client.ports import DriveMeta


@dataclass(frozen=True)
class Decision:
    """Allowed, or the reason it is not."""

    allowed: bool
    reason: str | None = None


def _resolve(
    node: str,
    metas: Mapping[str, DriveMeta | None],
    allowed: frozenset[str],
    depth: int,
    path: frozenset[str],
) -> str:
    """``allowed``, ``outside`` or ``unknown`` for one node over every parent chain."""
    if node in allowed:
        return "allowed"
    if depth <= 0 or node in path:
        return "unknown"  # too deep, or a cycle: never guess
    meta = metas.get(node)
    if meta is None:
        return "unknown"
    parents = meta.parents or ((meta.drive_id,) if meta.drive_id and meta.drive_id != node else ())
    if not parents:
        return "outside"  # a root that is not on the list
    results = {_resolve(p, metas, allowed, depth - 1, path | {node}) for p in parents}
    if "unknown" in results:
        return "unknown"
    return "allowed" if "allowed" in results else "outside"


def decide(
    file_id: str, metas: Mapping[str, DriveMeta | None], allowed: frozenset[str], max_depth: int
) -> Decision:
    """May ``file_id`` be read? ``metas`` holds every node the source returned (None or absent:
    not visible, which is unknown ancestry)."""
    meta = metas.get(file_id)
    if meta is None:
        return Decision(False, "unknown_ancestry")
    shortcut = meta.shortcut_target is not None
    target = meta.shortcut_target or file_id
    verdict = _resolve(target, metas, allowed, max_depth, frozenset())
    if verdict == "allowed":
        return Decision(True)
    if verdict == "unknown":
        return Decision(False, "unknown_ancestry")
    return Decision(False, "shortcut_outside" if shortcut else "outside_allow_list")


def ancestors_to_resolve(meta: DriveMeta) -> tuple[str, ...]:
    """The ids whose metadata the resolver needs next (parents, drive root, shortcut target)."""
    out = list(meta.parents)
    if meta.drive_id and meta.drive_id != meta.id:
        out.append(meta.drive_id)
    if meta.shortcut_target:
        out.append(meta.shortcut_target)
    return tuple(dict.fromkeys(out))
