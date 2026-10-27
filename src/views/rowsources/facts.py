"""Facts by grade: the Account Summary's Platform picture by lane
(``exports.readers.summary.map_summary``), cell for cell as ``exports.word.compose.picture_cells``
prints it, plus the lane. Grades are the stored grades as the grading engine labelled them (D-01
mapping), never recomputed here. Pure."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from exports.word.compose import picture_cells
from views.rowsources.common import ViewRow, build_row
from views.tables import TableSpec

_KEYS = ("ref", "item", "finding", "grade", "use", "reasoning")


def facts_rows(
    mapped: Mapping[str, Any], spec: TableSpec, urls: Mapping[str, str]
) -> list[ViewRow]:
    """One row per graded finding, lane by lane, in the document's order."""
    model = getattr(mapped.get("summary"), "model", None)
    out: list[ViewRow] = []
    for lane in getattr(model, "lanes", ()) or ():
        for r in lane.rows:
            values = dict(zip(_KEYS, picture_cells(r), strict=True))
            values["lane"] = lane.lane
            out.append(
                build_row(
                    spec, len(out) + 1, values, row_id=r.row_id, codes=tuple(r.codes), urls=urls
                )
            )
    return out
