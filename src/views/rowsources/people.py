"""People: the People List sheet's rows (``exports.readers.people.map_people``). Pure."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from views.rowsources.common import ViewRow, build_row
from views.tables import TableSpec


def people_rows(
    mapped: Mapping[str, Any], spec: TableSpec, urls: Mapping[str, str]
) -> list[ViewRow]:
    """One row per listed person, in the sheet's order."""
    model = getattr(mapped.get("people"), "model", None)
    rows = getattr(model, "rows", ()) or ()
    return [
        build_row(
            spec,
            i,
            r.model_dump(),
            row_id=r.row_id,
            codes=(r.source_code,),
            urls=urls,
            fallback_url=r.profile_url,
        )
        for i, r in enumerate(rows, start=1)
    ]
