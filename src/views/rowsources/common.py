"""What every row source shares: the row type, the documented cell formatting, and source
resolution through the run's Sources and Evidence registry. Pure."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from views.render_safe import safe_link
from views.tables import TableKind, TableSpec

Cell = str | int | None


def cell_value(value: Any) -> Cell:
    """A value exactly as the export prints it: None as empty text, booleans as Yes or No,
    dates as ISO dates, integers as integers, everything else as text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int):
        return value
    return str(value)


@dataclass(frozen=True)
class ViewRow:
    """One row of a client table."""

    table: TableKind
    position: int
    ref: str
    row_id: str
    cells: dict[str, Cell]
    source_code: str | None = None
    source_url: str | None = None
    search_parts: tuple[str, ...] = field(default=())

    @property
    def source_state(self) -> str:
        """``linked`` when the row resolves to a source code AND a URL; ``no_source`` else."""
        return "linked" if self.source_code and self.source_url else "no_source"

    @property
    def search_text(self) -> str:
        """The text full-text search sees (searchable cells only)."""
        return " ".join(p for p in self.search_parts if p)[:20000]


def source_urls(mapped: Mapping[str, Any]) -> dict[str, str]:
    """Source code -> URL from the Sources and Evidence model (only safe http(s) links)."""
    evidence = mapped.get("evidence")
    model = getattr(evidence, "model", None)
    out: dict[str, str] = {}
    for src in getattr(model, "sources", ()) or ():
        link = safe_link(src.url)
        if link:
            out[str(src.code)] = link
    return out


def build_row(
    spec: TableSpec,
    position: int,
    values: Mapping[str, Any],
    *,
    row_id: str,
    codes: tuple[str, ...],
    urls: Mapping[str, str],
    fallback_url: str | None = None,
) -> ViewRow:
    """A row from its values: cells in the spec's columns, the first code that resolves to a URL
    (or the row's own link when the registry has none for its code), searchable text."""
    cells = {c.key: cell_value(values.get(c.key)) for c in spec.columns}
    code = next((c for c in codes if c in urls), codes[0] if codes else None)
    url = urls.get(code) if code else None
    if code and not url:
        url = safe_link(fallback_url)
    searchable = tuple(str(cells[c.key]) for c in spec.columns if c.searchable)
    return ViewRow(
        table=spec.kind,
        position=position,
        ref=str(cells.get("ref") or ""),
        row_id=row_id,
        cells=cells,
        source_code=code,
        source_url=url,
        search_parts=searchable,
    )


def rows_hash(rows: Mapping[TableKind, list[ViewRow]]) -> str:
    """SHA-256 of every table's rows (canonical JSON), the materialisation's identity."""
    body = {
        table: [[r.position, r.ref, r.row_id, r.cells, r.source_code, r.source_url] for r in rs]
        for table, rs in sorted(rows.items())
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
