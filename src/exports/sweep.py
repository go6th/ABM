"""The last line of defence of a build: replace the strings of a render model that a client-output
rule refuses, then render again.

The mapping applies every rule to the texts it prints (``MapContext.rendered``, ``quote``,
``link_rule``) so the final scans of the produced bytes find nothing. A text path the mapping
missed (a new field, a label built from a stored value) is found by those scans only after a file
is rendered, and used to fail the whole research for it (staging 2026-10-31: the file build
refused the finished research of an account). ``sweep_model`` walks every string of the model,
asks ``blocked`` (the rule name, or None) and replaces a refused string by the withheld note.
Row keys, source codes and addresses are not repaired: a refused address cannot be replaced by
a note, so it is counted as ``unfixable`` and the caller withholds the file.

Pure. The counts are by rule, never the text.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

# fields that identify a row, a source or a cell and are never prose
_IDENTIFIERS = frozenset(
    {
        "ref",
        "row_id",
        "code",
        "codes",
        "source_code",
        "key",
        "posting_ref",
        "level_id",
        "lane_id",
        "number",
    }
)
_SHORTEST = 3  # a shorter string cannot hold a term, a tool name or an address


@dataclass
class SweepReport:
    """What one sweep did: strings replaced by rule, and refused strings it could not replace."""

    replaced: Counter[str] = field(default_factory=Counter)
    unfixable: Counter[str] = field(default_factory=Counter)


def _is_address(name: str) -> bool:
    return name == "url" or name.endswith("_url")


def sweep_model(
    model: BaseModel, blocked: Callable[[str], str | None], note: str
) -> tuple[BaseModel, SweepReport]:
    """``(model, report)``: ``model`` with every refused string replaced by ``note``. The same
    object when nothing was refused."""
    report = SweepReport()
    swept = _walk(model, blocked, note, report, "")
    assert isinstance(swept, BaseModel)
    return swept, report


def _walk(
    value: Any,
    blocked: Callable[[str], str | None],
    note: str,
    report: SweepReport,
    name: str,
) -> Any:
    if isinstance(value, str):
        if len(value) < _SHORTEST:
            return value
        rule = blocked(value)
        if rule is None:
            return value
        if _is_address(name):
            report.unfixable[rule] += 1  # an address is not replaced by a note
            return value
        report.replaced[rule] += 1
        return note
    if isinstance(value, BaseModel):
        updates: dict[str, Any] = {}
        for field_name in type(value).model_fields:
            if field_name in _IDENTIFIERS:
                continue
            old = getattr(value, field_name)
            new = _walk(old, blocked, note, report, field_name)
            if new is not old:
                updates[field_name] = new
        return value.model_copy(update=updates) if updates else value
    if isinstance(value, tuple | list):
        items = [_walk(v, blocked, note, report, name) for v in value]
        if all(a is b for a, b in zip(items, value, strict=True)):
            return value
        return type(value)(items) if isinstance(value, list) else tuple(items)
    if isinstance(value, dict):
        out = {k: _walk(v, blocked, note, report, name) for k, v in value.items()}
        return value if all(out[k] is value[k] for k in value) else out
    return value
