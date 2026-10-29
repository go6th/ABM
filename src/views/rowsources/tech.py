"""Tech stack: the Confirmed stack sheet's rows (``exports.readers.tech.map_tech``). Pure."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from views.rowsources.common import ViewRow, build_row
from views.tables import TableSpec


def tech_rows(mapped: Mapping[str, Any], spec: TableSpec, urls: Mapping[str, str]) -> list[ViewRow]:
    """One row per technology of the organisation's stack, in the sheet's order (the lookup's).
    A stack row cites no source page, so none carries a link."""
    model = getattr(mapped.get("tech"), "model", None)
    rows = getattr(model, "stack", ()) or ()
    return [
        build_row(spec, i, r.model_dump(), row_id=r.row_id, codes=(), urls=urls)
        for i, r in enumerate(rows, start=1)
    ]
