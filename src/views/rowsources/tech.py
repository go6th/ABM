"""Tech stack: the Detections sheet's rows (``exports.readers.tech.map_tech``). Pure."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from views.rowsources.common import ViewRow, build_row
from views.tables import TableSpec


def tech_rows(mapped: Mapping[str, Any], spec: TableSpec, urls: Mapping[str, str]) -> list[ViewRow]:
    """One row per detection, in the sheet's order."""
    model = getattr(mapped.get("tech"), "model", None)
    rows = getattr(model, "rows", ()) or ()
    return [
        build_row(spec, i, r.model_dump(), row_id=r.row_id, codes=(r.source_code,), urls=urls)
        for i, r in enumerate(rows, start=1)
    ]
