"""Facts by grade: the Account Summary's lane tables (``exports.readers.summary.map_summary``),
cell for cell as ``exports.word.compose.lane_cells`` prints them, plus the lane. Grades are the
stored grades as the grading engine labelled them (D-01 mapping), never recomputed here. Pure."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from exports.word.compose import lane_cells
from views.rowsources.common import ViewRow, build_row
from views.tables import TableSpec

_KEYS = ("ref", "finding", "grade", "use", "question", "sources")


def facts_rows(
    mapped: Mapping[str, Any], spec: TableSpec, urls: Mapping[str, str]
) -> list[ViewRow]:
    """One row per graded finding, lane by lane, in the document's order."""
    model = getattr(mapped.get("summary"), "model", None)
    out: list[ViewRow] = []
    for lane in getattr(model, "lanes", ()) or ():
        for r in lane.rows:
            values = dict(zip(_KEYS, lane_cells(r), strict=True))
            values["lane"] = lane.lane
            out.append(
                build_row(
                    spec, len(out) + 1, values, row_id=r.row_id, codes=tuple(r.codes), urls=urls
                )
            )
    return out
