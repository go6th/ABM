"""Coverage tracker: the Account Summary's tracker table (the tracker's client view), cell for
cell as ``exports.word.compose.tracker_cells`` prints it. A coverage row is search proof, not a
fact: it carries no source and is shown as such. Pure."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from exports.word.compose import tracker_cells
from views.rowsources.common import ViewRow, build_row
from views.tables import TableSpec

_KEYS = ("ref", "row", "status", "priority", "reason", "tools", "results")


def coverage_rows(mapped: Mapping[str, Any], spec: TableSpec) -> list[ViewRow]:
    """One row per coverage row, in the document's order."""
    model = getattr(mapped.get("summary"), "model", None)
    return [
        build_row(
            spec,
            i,
            dict(zip(_KEYS, tracker_cells(t), strict=True)),
            row_id=t.row_id,
            codes=(),
            urls={},
        )
        for i, t in enumerate(getattr(model, "tracker", ()) or (), start=1)
    ]
