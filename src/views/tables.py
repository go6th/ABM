"""The five client tables (VW1): their columns, kinds, filter operators and where each one lives
in the export file set. Built from the export configuration (``config/export_rules.yaml``), so a
column added to a workbook appears in the table and the parity check without a second edit.

* ``people``, ``jobs``, ``tech``: the workbook sheets (columns from ``workbooks.*.columns``).
* ``facts`` (Facts by grade): the Account Summary's lane tables (headers from
  ``documents.tables.lane``) plus the lane itself (a heading in the document, a column here).
* ``coverage``: the Account Summary's coverage tracker table (``documents.tables.tracker``).

Pure data.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from exports.config import ExportConfig, load_export_config

TableKind = Literal["people", "jobs", "tech", "facts", "coverage"]
TABLES: tuple[TableKind, ...] = ("people", "jobs", "tech", "facts", "coverage")
Op = Literal["eq", "ne", "contains", "starts_with", "in", "gte", "lte", "is_empty", "not_empty"]

# Operators allowed per column kind (VW3: allow-listed; nothing else reaches SQL).
OPS_BY_KIND: dict[str, frozenset[Op]] = {
    "ref": frozenset({"eq", "in", "starts_with"}),
    "text_short": frozenset({"eq", "ne", "contains", "starts_with", "is_empty", "not_empty"}),
    "text_long": frozenset({"contains", "is_empty", "not_empty"}),
    "label": frozenset({"eq", "ne", "in", "is_empty", "not_empty"}),
    "url": frozenset({"contains", "is_empty", "not_empty"}),
    "code": frozenset({"eq", "in", "is_empty", "not_empty"}),
    "date": frozenset({"eq", "gte", "lte", "is_empty", "not_empty"}),
    "count": frozenset({"eq", "gte", "lte"}),
}
SORTABLE = frozenset({"ref", "text_short", "label", "code", "date"})
SEARCHABLE = frozenset({"text_short", "text_long", "label", "code"})


@dataclass(frozen=True)
class ViewColumn:
    """One column of a client table."""

    key: str
    header: str
    kind: str
    exported: bool = True

    @property
    def ops(self) -> frozenset[Op]:
        """The filter operators this column accepts."""
        return OPS_BY_KIND[self.kind]

    @property
    def sortable(self) -> bool:
        """Whether the column may be sorted on."""
        return self.kind in SORTABLE

    @property
    def searchable(self) -> bool:
        """Whether full-text search covers the column."""
        return self.kind in SEARCHABLE


@dataclass(frozen=True)
class TableSpec:
    """One client table and its place in the export file set."""

    kind: TableKind
    file_kind: Literal["people", "jobs", "tech", "summary"]
    sheet: str | None
    word_table: str | None
    columns: tuple[ViewColumn, ...]

    def column(self, key: str) -> ViewColumn | None:
        """The column with this key, if the table has one."""
        return next((c for c in self.columns if c.key == key), None)

    @property
    def exported_columns(self) -> tuple[ViewColumn, ...]:
        """Columns the export file prints (the parity check compares these)."""
        return tuple(c for c in self.columns if c.exported)


_LANE_KEYS = ("ref", "finding", "grade", "use", "question", "sources")
_LANE_KINDS = ("ref", "text_long", "label", "label", "text_long", "code")
_TRACKER_KEYS = ("ref", "row", "status", "priority", "reason", "tools", "results")
_TRACKER_KINDS = ("ref", "text_short", "label", "label", "text_short", "label", "text_short")


def build_specs(cfg: ExportConfig) -> dict[TableKind, TableSpec]:
    """Every table spec from one export configuration."""
    books = cfg.rules.workbooks
    headers = cfg.rules.documents.tables

    def book(kind: TableKind, b: object) -> TableSpec:
        cols = tuple(
            ViewColumn(c.key, c.header, c.kind)
            for c in b.columns  # type: ignore[attr-defined]
        )
        return TableSpec(kind, kind, b.sheet, None, cols)  # type: ignore[arg-type, attr-defined]

    lane = tuple(
        ViewColumn(k, h, kind)
        for k, h, kind in zip(_LANE_KEYS, headers["lane"], _LANE_KINDS, strict=True)
    )
    tracker = tuple(
        ViewColumn(k, h, kind)
        for k, h, kind in zip(_TRACKER_KEYS, headers["tracker"], _TRACKER_KINDS, strict=True)
    )
    return {
        "people": book("people", books.people),
        "jobs": book("jobs", books.jobs),
        "tech": book("tech", books.tech),
        "facts": TableSpec(
            "facts",
            "summary",
            None,
            "lane",
            (*lane, ViewColumn("lane", "Lane", "label", exported=False)),
        ),
        "coverage": TableSpec("coverage", "summary", None, "tracker", tracker),
    }


@lru_cache
def specs() -> dict[TableKind, TableSpec]:
    """The table specs of the platform's export configuration."""
    return build_specs(load_export_config())
