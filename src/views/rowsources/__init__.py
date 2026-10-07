"""Row sources (VW1): the five client tables as typed rows, produced from the ONE mapping the
export builders render (``exports.builder.map_snapshot``). The API, the on-screen tables and the
parity check all read these rows; the export files are rendered from the same mapped models.

``rows_of(mapped)`` returns every table's rows in file order. Each row carries its cells exactly
as the export prints them (``cell_value``: the documented formatting), its source code and the
source URL resolved through the Sources and Evidence registry, and a source state: ``linked``
when both resolve, ``no_source`` otherwise (VW2: such a row is never shown as a fact).
Pure.

GUARANTEE(test_every_row_links_to_its_source_or_says_it_has_none)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from views.rowsources.common import ViewRow, source_urls
from views.rowsources.coverage import coverage_rows
from views.rowsources.facts import facts_rows
from views.rowsources.jobs import jobs_rows
from views.rowsources.people import people_rows
from views.rowsources.tech import tech_rows
from views.tables import TableKind, TableSpec, specs


def rows_of(
    mapped: Mapping[str, Any], table_specs: Mapping[TableKind, TableSpec] | None = None
) -> dict[TableKind, list[ViewRow]]:
    """Every client table's rows from one mapped snapshot."""
    sp = table_specs or specs()
    urls = source_urls(mapped)
    return {
        "people": people_rows(mapped, sp["people"], urls),
        "jobs": jobs_rows(mapped, sp["jobs"], urls),
        "tech": tech_rows(mapped, sp["tech"], urls),
        "facts": facts_rows(mapped, sp["facts"], urls),
        "coverage": coverage_rows(mapped, sp["coverage"]),
    }


__all__ = ["ViewRow", "rows_of"]
