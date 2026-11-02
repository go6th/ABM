"""Tables are queried, not retrieved (2026-10-17): the ``describe_table`` and ``query_table``
tools over the table store (migration 0077).

A question that is a filter, count or lookup over a spreadsheet or CRM export (``List all open
Salesforce-related jobs first seen on or after March 1, 2026``) was routed through passage
retrieval and answered from a top-k sample: the wrong rows, double counted, and different on
every run. Here the model names the table and its predicates, and the store answers with the
complete set of matching rows, typed and in row order, so the same question gives the same
answer every time.

* ``list_tables``: the tables of the caller's accounts (row level security plus an explicit
  account scope), each with a name the model uses (the sheet's name; the document's name is
  added when two documents share one).
* ``describe``: columns with the name as written, the normalised name the predicates use, the
  inferred type (``date``, ``number`` or ``text``), whether the column is payload, three sample
  values per column (80 characters at most), the business key and the row count. The model
  reads it before it queries a table it has not seen in the turn.
* ``query``: ``where`` predicates (the closed ``op`` set of ``rag.retrieval``), ``columns``,
  ``order_by``, ``distinct_on`` (the business key: duplicate rows collapse to the one seen
  last; a row without the key merges onto the keyed row of the same title and a shared place
  word, else falls back to title plus location) and ``limit``.
  Every column name is checked against the schema and every value is a bound parameter; the SQL
  text never carries the caller's words. Unknown columns, operators or values are a
  ``QueryError`` the model reads and corrects, never a 500. More than ``MAX_FILTER_ROWS``
  matches is an error too (narrow the filter), never a silent cut; ``truncated`` is true only
  when ``limit`` was hit.
* Every row carries ``{table, row, member}`` so ``documents.chunking.cite`` cites it unchanged,
  and its cells under ``cells``. Cells were redacted before they were stored (``Redactor.cell``
  in the chunker), so nothing leaves here that the passages do not hold.

GUARANTEE(test_a_tabular_question_is_answered_from_the_table_store_not_a_sample)

GUARANTEE(test_query_table_returns_complete_sets)

GUARANTEE(test_query_table_refuses_unknown_columns)
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from account_intelligence.rag.retrieval import MAX_FILTER_ROWS, OPS, typed
from documents import expanders as EXP
from documents import repo
from documents.chunking import list_items

SAMPLE_VALUES = 3
SAMPLE_CHARS = 80
SAMPLE_ROWS = 50
DEFAULT_LIMIT = 1000
MAX_LIMIT = 5000
READ_ROWS_MAX = 50  # a read_rows call reads whole rows, payload cells included
# the columns a row without its business key collapses on instead
FALLBACK_KEY = ("title", "location")

DESCRIBE_TABLE_TOOL: dict[str, Any] = {
    "name": "describe_table",
    "description": (
        "Columns, inferred types, row count and three sample values per column of an ingested"
        " spreadsheet or CRM export. Call it before query_table on a table you have not seen in"
        " this turn; use the normalised column names it returns in query_table."
    ),
    "input_schema": {
        "type": "object",
        "required": ["table"],
        "additionalProperties": False,
        "properties": {"table": {"type": "string"}},
    },
}
QUERY_TABLE_TOOL: dict[str, Any] = {
    "name": "query_table",
    "description": (
        "Filter/project rows of an ingested spreadsheet or CRM export. Use for any question that"
        " is a filter, count, or lookup over tabular data. Returns complete result sets, never a"
        " top-k sample."
    ),
    "input_schema": {
        "type": "object",
        "required": ["table"],
        "additionalProperties": False,
        "properties": {
            "table": {"type": "string"},
            "where": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["column", "op", "value"],
                    "additionalProperties": False,
                    "properties": {
                        "column": {"type": "string"},
                        "op": {"enum": list(OPS)},
                        "value": {},
                    },
                },
            },
            "columns": {"type": "array", "items": {"type": "string"}},
            "order_by": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["column"],
                    "additionalProperties": False,
                    "properties": {"column": {"type": "string"}, "desc": {"type": "boolean"}},
                },
            },
            "distinct_on": {"type": "string"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_LIMIT,
                "default": DEFAULT_LIMIT,
            },
            "radius_miles": {
                "type": "number",
                "minimum": 1,
                "description": (
                    "A filter on a place column also returns, as `related`, the rows whose place"
                    " lies within this many miles (default 50), nearest first with the distance;"
                    " pass it only when the question names another distance."
                ),
            },
            "expand": {
                "type": "boolean",
                "description": (
                    "Default true: a text filter on a spelling in an alias group (SFDC, VP) also"
                    " matches the group's other spellings, and a place filter also returns nearby"
                    " rows. Pass false only when the question asks for the exact words alone."
                ),
            },
        },
    },
}
READ_ROWS_TOOL: dict[str, Any] = {
    "name": "read_rows",
    "description": (
        "Whole rows of an ingested table by row number, payload columns (the long cells"
        " query_table leaves out) included. For evidence reading after a filter: quote the"
        " deciding phrase from the description or platform-sentences column of the rows a"
        " query returned."
    ),
    "input_schema": {
        "type": "object",
        "required": ["table", "rows"],
        "additionalProperties": False,
        "properties": {
            "table": {"type": "string"},
            "rows": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "minItems": 1,
                "maxItems": READ_ROWS_MAX,
            },
            "columns": {"type": "array", "items": {"type": "string"}},
        },
    },
}
TOOLS: tuple[dict[str, Any], ...] = (DESCRIBE_TABLE_TOOL, QUERY_TABLE_TOOL, READ_ROWS_TOOL)


class QueryError(Exception):
    """A request the store refuses, as the model reads it: a code, a message and the detail
    it needs to correct the call (the known columns, the allowed operators)."""

    def __init__(self, code: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail

    def payload(self) -> dict[str, Any]:
        """The structured error the tool result carries."""
        return {"error": {"code": self.code, "message": self.message, **self.detail}}


@dataclass(frozen=True)
class ColumnInfo:
    """One column as the store knows it."""

    name: str
    raw: str
    type: str
    payload: bool
    multi_valued: bool = False
    # the router's lexicon (generated at ingest): the column's natural forms, and the distinct
    # values of a low-cardinality text column; empty for tables ingested before the lexicon
    aliases: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    # False: the router never takes a word of the question as naming this column (the research
    # tables' synthetic ``account`` and ``file_row``: every table has them)
    routed: bool = True


@dataclass(frozen=True)
class ViewSource:
    """Where a research table's rows live: the client table ``kind`` (people, jobs, tech, ...) of
    the delivered runs of these accounts in ``view_rows`` (``views.materialize``) — the rows of
    the delivered files, row for row, all accounts in one table with an ``account`` column. An
    uploaded table has none (its rows are in ``document_table_rows``)."""

    kind: str
    run_ids: tuple[UUID, ...]
    account_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class TableInfo:
    """One table of the workspace: an ingested upload, or a research table (``view``)."""

    id: UUID
    name: str  # the name the model uses (unique in the workspace)
    table: str  # the location's table string (what a passage cites)
    member: str | None
    document: str
    document_id: UUID
    account_id: UUID
    columns: tuple[ColumnInfo, ...]
    business_key: str | None
    row_count: int
    view: ViewSource | None = None

    @property
    def names(self) -> dict[str, ColumnInfo]:
        """Columns by normalised name."""
        return {c.name: c for c in self.columns}


@dataclass(frozen=True)
class Where:
    """One validated predicate, bound to a column of the schema."""

    column: ColumnInfo
    op: str
    value: Any
    # the other spellings of an alias group (documents.expanders): the typed value still matches
    # as before, these match as whole words
    also: tuple[str, ...] = ()


@dataclass
class Query:
    """A validated ``query_table`` call."""

    table: TableInfo
    where: list[Where] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    order_by: list[tuple[str, bool]] = field(default_factory=list)
    distinct_on: str | None = None
    limit: int = DEFAULT_LIMIT
    # the answer expanders (documents.expanders): on unless the call turns them off
    expand: bool = True
    radius_miles: float | None = None
    expansions: list[dict[str, Any]] = field(default_factory=list)

    def applied_where(self) -> list[dict[str, Any]]:
        """The predicates as applied (normalised column names, JSON values)."""
        return [{"column": w.column.name, "op": w.op, "value": _json(w.value)} for w in self.where]


def _json(value: Any) -> Any:
    """A typed predicate value as JSON: dates as ISO strings, whole floats as ints."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, list):
        return [_json(v) for v in value]
    return value


def _info(row: Any, name: str) -> TableInfo:
    return TableInfo(
        id=UUID(str(row.id)),
        name=name,
        table=str(row.table_name),
        member=str(row.member) if row.member else None,
        document=str(row.document),
        document_id=UUID(str(row.document_id)),
        account_id=UUID(str(row.account_id)),
        columns=tuple(
            ColumnInfo(
                str(c.get("name")),
                str(c.get("raw", c.get("name"))),
                str(c.get("type", "text")),
                bool(c.get("payload")),
                bool(c.get("multi_valued")),
                tuple(str(a) for a in c.get("aliases") or ()),
                tuple(str(v) for v in c.get("values") or ()),
            )
            for c in (row.columns or [])
        ),
        business_key=str(row.business_key) if row.business_key else None,
        row_count=int(row.row_count or 0),
    )


async def list_tables(session: Any, accounts: Sequence[UUID]) -> list[TableInfo]:
    """The tables of these accounts (the caller's own: row level security limits the rows to
    the tenant and the owner), with names unique in the workspace: a sheet name two documents
    share is suffixed with the document's name."""
    rows = await repo.tables_for(session, accounts)
    counts: dict[str, int] = {}
    for r in rows:
        counts[str(r.table_name)] = counts.get(str(r.table_name), 0) + 1
    out: list[TableInfo] = []
    taken: set[str] = set()
    for r in rows:
        name = str(r.table_name)
        if counts[name] > 1:
            name = f"{name} ({r.document})"
        base, n = name, 1
        # LOOP(unique table name): cap=the workspace's tables; exit=a name no table has
        while name in taken:
            n += 1
            name = f"{base} #{n}"
        taken.add(name)
        out.append(_info(r, name))
    return out


def find_table(tables: Sequence[TableInfo], name: str) -> TableInfo:
    """The table the model named (exact, then case-insensitive, then by the sheet name alone);
    ``QueryError`` naming the known tables otherwise."""
    wanted = name.strip()
    for t in tables:
        if t.name == wanted:
            return t
    folded = wanted.casefold()
    for t in tables:
        if t.name.casefold() == folded or t.table.casefold() == folded:
            return t
    for t in tables:
        if t.table.casefold().endswith(":" + folded):
            return t
    raise QueryError(
        "unknown_table",
        f"No table named {wanted!r}; the tables are: {', '.join(t.name for t in tables) or 'none'}",
        tables=[t.name for t in tables],
    )


def _sample(value: Any) -> Any:
    if isinstance(value, str) and len(value) > SAMPLE_CHARS:
        return value[: SAMPLE_CHARS - 1] + "…"
    return value


async def describe(session: Any, table: TableInfo) -> dict[str, Any]:
    """The table as the model reads it before querying: columns (name as written, the
    normalised name predicates use, type, payload), three sample values each from the first
    rows (80 characters at most), the business key and the row count."""
    rows = await repo.table_rows(session, table.id, "", {}, SAMPLE_ROWS, view=table.view)
    samples: dict[str, list[Any]] = {c.name: [] for c in table.columns}
    for r in rows:
        cells = r.cells or {}
        for name, found in samples.items():
            value = cells.get(name)
            if value is None or len(found) >= SAMPLE_VALUES or value in found:
                continue
            found.append(value)
    return {
        "table": table.name,
        "document": table.document,
        "member": table.member,
        "row_count": table.row_count,
        "business_key": table.business_key,
        "columns": [
            {
                "name": c.name,
                "header": c.raw,
                "type": c.type,
                "payload": c.payload,
                "multi_valued": c.multi_valued,
                "samples": [_sample(v) for v in samples[c.name]],
            }
            for c in table.columns
        ],
        "hint": (
            "Use the normalised column names in query_table; a payload column is not shown"
            " unless named in columns (read_rows shows whole rows, payload included). Dates are"
            " ISO (YYYY-MM-DD). On a multi_valued column, contains/icontains match a whole list"
            " item first, a substring second (substring-only rows come back partial=true)."
        ),
    }


def _column(table: TableInfo, name: Any) -> ColumnInfo:
    key = str(name).strip()
    known = table.names
    if key in known:
        return known[key]
    folded = key.casefold().replace(" ", "_")
    if folded in known:
        return known[folded]
    for c in table.columns:
        if c.raw.casefold() == key.casefold():
            return c
    raise QueryError(
        "unknown_column",
        f"The table has no column {key!r}; its columns are: {', '.join(known)}",
        columns=list(known),
    )


def _scalar(column: ColumnInfo, value: Any) -> Any:
    """``value`` as the column's type, or ``QueryError`` when it cannot be read as one."""
    if value is None:
        raise QueryError("bad_value", f"{column.name}: a value is required for this op")
    if column.type == "date":
        got = typed(value)
        if not isinstance(got, date):
            raise QueryError(
                "bad_value", f"{column.name} is a date column: give an ISO date (YYYY-MM-DD)"
            )
        return got
    if column.type == "number":
        got = typed(value)
        if not isinstance(got, float):
            raise QueryError("bad_value", f"{column.name} is a number column: give a number")
        return got
    return str(value)


def parse(table: TableInfo, payload: Mapping[str, Any]) -> Query:
    """A validated query from the tool call: every column whitelisted against the schema,
    every operator from the closed set, values typed to their column, the limit bounded."""
    query = Query(table)
    if payload.get("expand") is False:
        query.expand = False
    asked_radius = payload.get("radius_miles")
    if asked_radius is not None:
        if isinstance(asked_radius, bool) or not isinstance(asked_radius, int | float):
            raise QueryError("bad_radius", "radius_miles must be a number of miles")
        query.radius_miles = EXP.radius(asked_radius)
    where = payload.get("where") or []
    if not isinstance(where, list):
        raise QueryError("bad_where", "where must be a list of {column, op, value}")
    for item in where:
        if not isinstance(item, Mapping) or "column" not in item or "op" not in item:
            raise QueryError("bad_where", "each where item needs column and op (and a value)")
        op = str(item["op"])
        if op not in OPS:
            raise QueryError("unknown_op", f"unknown op {op!r}; one of {', '.join(OPS)}", ops=OPS)
        column = _column(table, item["column"])
        value: Any = item.get("value")
        if op in ("is_null", "not_null"):
            value = None
        elif op == "in":
            if not isinstance(value, list) or not value:
                raise QueryError("bad_value", f"{column.name}: 'in' takes a non-empty list")
            value = [_scalar(column, v) for v in value]
        elif op in ("contains", "icontains"):
            if value is None or str(value) == "":
                raise QueryError("bad_value", f"{column.name}: contains needs a value")
            value = str(value)
            also = (
                EXP.aliases_of(value)
                if query.expand and op == "icontains" and column.type == "text"
                else []
            )
            if also:
                # the typed text still matches as a substring; the group's other spellings
                # match as whole words (documents.expanders)
                query.expansions.append({"column": column.name, "term": value, "also": also})
                query.where.append(Where(column, op, value, tuple(also)))
                continue
        else:
            value = _scalar(column, value)
        query.where.append(Where(column, op, value))
    columns = payload.get("columns") or []
    if not isinstance(columns, list):
        raise QueryError("bad_columns", "columns must be a list of column names")
    query.columns = [_column(table, c).name for c in columns]
    order = payload.get("order_by") or []
    if not isinstance(order, list):
        raise QueryError("bad_order", "order_by must be a list of {column, desc}")
    for item in order:
        if not isinstance(item, Mapping) or "column" not in item:
            raise QueryError("bad_order", "each order_by item needs a column")
        query.order_by.append((_column(table, item["column"]).name, bool(item.get("desc"))))
    if payload.get("distinct_on"):
        query.distinct_on = _column(table, payload["distinct_on"]).name
    limit = payload.get("limit", DEFAULT_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise QueryError("bad_limit", f"limit must be an integer between 1 and {MAX_LIMIT}")
    query.limit = limit
    return query


def _sql(query: Query) -> tuple[str, dict[str, Any]]:
    """The WHERE fragment over the row alias ``r`` and its bound parameters. Column names
    reach the SQL only as bound jsonb keys; values only as bound parameters."""
    parts: list[str] = []
    params: dict[str, Any] = {}
    for i, w in enumerate(query.where):
        key = f"c{i}"
        params[key] = w.column.name
        raw = f"(r.cells ->> :{key})"
        if w.op == "is_null":
            parts.append(f"({raw} IS NULL OR {raw} = '')")
            continue
        if w.op == "not_null":
            parts.append(f"({raw} IS NOT NULL AND {raw} <> '')")
            continue
        val = f"v{i}"
        if w.op in ("contains", "icontains"):
            params[val] = w.value
            if w.also:
                params[f"{val}_re"] = EXP.word_pattern(w.also)
                parts.append(
                    f"(position(lower(:{val}) IN lower(coalesce({raw}, ''))) > 0"
                    f" OR lower(coalesce({raw}, '')) ~ :{val}_re)"
                )
                continue
            if w.op == "icontains":
                parts.append(f"position(lower(:{val}) IN lower(coalesce({raw}, ''))) > 0")
            else:
                parts.append(f"position(:{val} IN coalesce({raw}, '')) > 0")
            continue
        if w.column.type == "date":
            # a blank cell (a research table prints a missing date as "") is no date, not an error
            expr, bind = f"CAST(NULLIF({raw}, '') AS date)", f"CAST(:{val} AS date)"
        elif w.column.type == "number":
            expr, bind = f"CAST(NULLIF({raw}, '') AS numeric)", f"CAST(:{val} AS numeric)"
        else:
            expr, bind = f"lower({raw})", f"lower(:{val})"
        if w.op == "in":
            params[val] = [_bound(v) for v in w.value]
            if w.column.type == "date":
                parts.append(f"{expr} = ANY(CAST(:{val} AS date[]))")
            elif w.column.type == "number":
                parts.append(f"{expr} = ANY(CAST(:{val} AS numeric[]))")
            else:
                parts.append(f"{expr} = ANY(CAST(:{val} AS text[]))")
            continue
        params[val] = _bound(w.value)
        sign = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[w.op]
        parts.append(f"({raw} IS NOT NULL AND {expr} {sign} {bind})")
    return " AND ".join(parts), params


def _bound(value: Any) -> Any:
    """A predicate value as the driver binds it for its cast: a date stays a date, a number
    binds as a Decimal (the numeric cast), text case folded (compared through ``lower``)."""
    if isinstance(value, date):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return str(value).casefold()


def _sort_key(value: Any) -> tuple[int, Any]:
    """None last; dates and numbers by value; text case folded."""
    got = typed(value)
    if got is None:
        return (1, "")
    if isinstance(got, date):
        return (0, got.isoformat())
    if isinstance(got, float):
        return (0, got)
    return (0, str(got).casefold())


# geographic filler words that say nothing about which posting a row is ("Europe", "Northern
# America"): a location match never rests on them
_GEO_NOISE = frozenset(
    {
        "europe",
        "asia",
        "americas",
        "america",
        "africa",
        "oceania",
        "emea",
        "apac",
        "latam",
        "northern",
        "southern",
        "western",
        "eastern",
        "central",
        "north",
        "south",
        "east",
        "west",
        "united",
        "states",
        "kingdom",
        "region",
    }
)


def _location_tokens(value: Any) -> frozenset[str]:
    """The words a location shares with another spelling of the same place ("Espoo, Uusimaa,
    Finland" and "Finland, Northern Europe, Europe" share ``finland``; geographic filler is
    ignored)."""
    return frozenset(
        w for w in re.findall(r"[a-z]{4,}", str(value or "").casefold()) if w not in _GEO_NOISE
    )


def _dedupe(
    rows: list[dict[str, Any]],
    key: str,
    prefer: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """One row per business key: the one with the latest ``last_seen`` (else the lowest row
    number; ``prefer(row, held)`` replaces that choice — ``documents.tiers`` keeps the earliest
    posting). A row without the key — a re-scrape that lost its id — merges onto the keyed row
    of the same title whose location shares a real place word (the Finland posting re-scraped
    as Espoo collapses onto its requisition), and apart from that falls back to title plus
    location among themselves, else stays as it is. Returns the survivors in row order and how
    many were removed."""
    _later = prefer or _latest_seen
    best: dict[Any, dict[str, Any]] = {}
    # pass 1: the keyed rows, and which title belongs to which keyed group with which places
    title_groups: dict[str, set[Any]] = {}
    group_places: dict[Any, frozenset[str]] = {}
    for row in rows:
        cells = row["cells"]
        value = cells.get(key)
        if value is None or str(value).strip() == "":
            continue
        group: Any = ("key", str(value).strip().casefold())
        held = best.get(group)
        if held is None or _later(row, held):
            best[group] = row
        title = str(cells.get("title") or "").strip().casefold()
        if title:
            title_groups.setdefault(title, set()).add(group)
        group_places[group] = group_places.get(group, frozenset()) | _location_tokens(
            cells.get("location")
        )
    # pass 2: the keyless rows
    for row in rows:
        cells = row["cells"]
        value = cells.get(key)
        if value is not None and str(value).strip():
            continue
        title = str(cells.get("title") or "").strip().casefold()
        places = _location_tokens(cells.get("location"))
        candidates = sorted(title_groups.get(title, ()), key=str)
        group = next(
            (g for g in candidates if places and group_places.get(g, frozenset()) & places), None
        )
        if group is None:
            fallback = tuple(str(cells.get(c) or "").casefold() for c in FALLBACK_KEY if c in cells)
            if not title or not any(fallback):
                best[("row", row["row"])] = row
                continue
            group = ("fallback", fallback)
        held = best.get(group)
        if held is None or _later(row, held):
            best[group] = row
    keep = {id(r) for r in best.values()}
    survivors = [r for r in rows if id(r) in keep]
    return survivors, len(rows) - len(survivors)


def _latest_seen(row: dict[str, Any], held: dict[str, Any]) -> bool:
    a, b = typed(row["cells"].get("last_seen")), typed(held["cells"].get("last_seen"))
    if isinstance(a, date) and isinstance(b, date) and a != b:
        return a > b
    if isinstance(a, date) != isinstance(b, date):
        return isinstance(a, date)
    return bool(row["row"] < held["row"])


async def run(session: Any, query: Query) -> dict[str, Any]:
    """Execute a validated query: the complete set of matching rows (``QueryError`` past
    ``MAX_FILTER_ROWS``: narrow the filter), deduplicated on ``distinct_on``, ordered, projected
    and bounded by ``limit`` (``truncated`` only then). On a multi-valued column, a
    ``contains``/``icontains`` predicate matches a whole list item first and a substring second;
    a row that matched by substring alone comes back ``partial=true``. An empty result carries
    ``next``: the relax-one-predicate directive the model is to follow before answering."""
    where, params = _sql(query)
    found = await repo.table_rows(
        session, query.table.id, where, params, MAX_FILTER_ROWS + 1, view=query.table.view
    )
    if len(found) > MAX_FILTER_ROWS:
        raise QueryError(
            "filter_too_wide",
            f"more than {MAX_FILTER_ROWS} rows match; narrow the filter (add a predicate)",
            max_rows=MAX_FILTER_ROWS,
        )
    rows: list[dict[str, Any]] = [
        {
            "table": query.table.table,
            "row": int(r.row_no),
            **({"member": query.table.member} if query.table.member else {}),
            "cells": dict(r.cells or {}),
        }
        for r in found
    ]
    matched_numbers = {int(r.row_no) for r in found}  # every row the filter matched, duplicates too
    removed = 0
    if query.distinct_on:
        rows, removed = _dedupe(rows, query.distinct_on)
    multi = [w for w in query.where if w.column.multi_valued and w.op in ("contains", "icontains")]
    partial = 0
    for row in rows:
        if any(not _whole_item(w, row["cells"].get(w.column.name)) for w in multi):
            row["partial"] = True
            partial += 1
    for column, desc in reversed(query.order_by):  # stable sorts: the first key wins

        def by(row: dict[str, Any], name: str = column) -> tuple[int, Any]:
            """The stable sort key of one row for this column (binds the loop's column)."""
            return _sort_key(row["cells"].get(name))

        rows.sort(key=by, reverse=desc)
    matched = len(rows)
    exact_rows = [dict(r) for r in rows]  # whole cells, before the projection below
    truncated = matched > query.limit
    rows = rows[: query.limit]
    shown = query.columns or [c.name for c in query.table.columns if not c.payload]
    for row in rows:
        row["cells"] = {name: row["cells"].get(name) for name in shown}
    out: dict[str, Any] = {
        "table": query.table.name,
        "rows": rows,
        "row_count": matched,
        "truncated": truncated,
        "applied_where": query.applied_where(),
    }
    if query.distinct_on:
        out["deduped_on"] = query.distinct_on
        out["dedup_removed"] = removed
    if multi:
        out["partial_rows"] = partial
    if query.expansions:
        out["expanded"] = query.expansions
    if query.expand:
        related = await _nearby(session, query, matched_numbers, exact_rows, shown)
        if related is not None:
            out["related"] = related
    if matched == 0 and query.where:
        # the directive the TABULAR_RULES promise at the point the decision is made (the model
        # reads the result, then decides): a cloud sampler at near-tie logits goes both ways on
        # prose rules; data it just received carries further
        out["next"] = (
            "0 rows matched. Before answering, relax exactly one predicate (status first, then"
            " date) and query again with the same columns; then state the finding and name the"
            " predicate you relaxed."
        )
    return out


RELATED_ROWS_MAX = 1000


async def _nearby(
    session: Any,
    query: Query,
    matched: set[int],
    exact_rows: list[dict[str, Any]],
    shown: Sequence[str],
) -> dict[str, Any] | None:
    """The nearby expander (``documents.expanders``): for the query's first filter on a place
    column, the rows OUTSIDE the exact result that lie within the radius of the place asked for,
    nearest first. ``matched`` is every row number the filter matched (a duplicate that
    ``distinct_on`` removed is not "nearby"); the query runs again without the place filter (every
    other filter kept). None when there is no place filter, ``{"skipped": ...}`` when the place
    or the row set cannot be used. The place is resolved first, so a filter on "Remote" or on a
    column that only looks like a place costs no second query."""
    w = next(
        (
            w
            for w in query.where
            if w.op in ("eq", "contains", "icontains")
            and EXP.is_place_column(w.column.name, w.column.raw)
        ),
        None,
    )
    if w is None:
        return None
    if EXP.resolve_target(str(w.value)) is None:
        return {"skipped": "target_is_not_a_city", "column": w.column.name}
    relaxed = Query(query.table, [x for x in query.where if x is not w])
    where, params = _sql(relaxed)
    found = await repo.table_rows(
        session, query.table.id, where, params, MAX_FILTER_ROWS + 1, view=query.table.view
    )
    if len(found) > MAX_FILTER_ROWS:
        return {"skipped": "too_many_rows", "column": w.column.name}
    candidates = [
        {
            "table": query.table.table,
            "row": int(r.row_no),
            **({"member": query.table.member} if query.table.member else {}),
            "cells": dict(r.cells or {}),
        }
        for r in found
        if int(r.row_no) not in matched
    ]
    if query.distinct_on:
        # one row per business key across the exact result AND the candidates: a posting whose
        # current row is in the exact set is not listed again for an older row of another place
        kept = {int(r["row"]) for r in exact_rows}
        survivors, _ = _dedupe(exact_rows + candidates, query.distinct_on)
        candidates = [r for r in survivors if int(r["row"]) not in kept]
    result = EXP.nearby(str(w.value), candidates, w.column.name, EXP.radius(query.radius_miles))
    if result is None:
        return {"skipped": "target_is_not_a_city", "column": w.column.name}
    rows = result["rows"]
    cap = min(query.limit, RELATED_ROWS_MAX)
    result["truncated"] = len(rows) > cap
    result["rows"] = [
        {**r, "cells": {name: r["cells"].get(name) for name in shown}} for r in rows[:cap]
    ]
    result["column"] = w.column.name
    return result


def _whole_item(where: Where, cell: Any) -> bool:
    """Whether the cell matches the contains predicate as a whole list item (``True``) or only
    as a substring (``False``: the row is ``partial``)."""
    if cell is None:
        return False
    items = list_items(str(cell))
    if not items:
        return False
    if where.op == "icontains":
        wanted = {str(where.value).casefold(), *where.also}
        return any(item.casefold() in wanted for item in items)
    return any(item == str(where.value) for item in items)


def parse_read(table: TableInfo, payload: Mapping[str, Any]) -> tuple[list[int], list[str]]:
    """A validated ``read_rows`` call: at most ``READ_ROWS_MAX`` unique row numbers and an
    optional projection, every column whitelisted (payload columns allowed: that is the tool's
    point)."""
    rows_in = payload.get("rows")
    if not isinstance(rows_in, list) or not rows_in:
        raise QueryError("bad_rows", "rows must be a non-empty list of row numbers")
    if len(rows_in) > READ_ROWS_MAX:
        raise QueryError(
            "bad_rows", f"at most {READ_ROWS_MAX} rows per call", max_rows=READ_ROWS_MAX
        )
    row_nos: list[int] = []
    for n in rows_in:
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise QueryError("bad_rows", f"{n!r} is not a row number")
        if n not in row_nos:
            row_nos.append(n)
    cols_in = payload.get("columns") or []
    if not isinstance(cols_in, list):
        raise QueryError("bad_columns", "columns must be a list of column names")
    columns = list(dict.fromkeys(_column(table, c).name for c in cols_in))
    return row_nos, columns


async def read(
    session: Any, table: TableInfo, row_nos: Sequence[int], columns: Sequence[str]
) -> dict[str, Any]:
    """The whole rows ``read_rows`` asked for: every cell, payload included (or the named
    columns), each row carrying its citation place; row numbers the store does not hold are
    listed as ``not_found`` and never fail the call."""
    found = await repo.table_rows_by_no(session, table.id, list(row_nos), view=table.view)
    seen: dict[int, Any] = {int(r.row_no): r for r in found}
    shown = list(columns) or [c.name for c in table.columns]
    rows = [
        {
            "table": table.table,
            "row": n,
            **({"member": table.member} if table.member else {}),
            "cells": {name: (seen[n].cells or {}).get(name) for name in shown},
        }
        for n in row_nos
        if n in seen
    ]
    return {
        "table": table.name,
        "rows": rows,
        "row_count": len(rows),
        "not_found": [n for n in row_nos if n not in seen],
        "columns": [c.raw for c in table.columns if c.name in shown],
    }


def matches_name(pattern: str, name: str) -> bool:
    """Whether a table name matches a per-table rule (a glob)."""
    return fnmatch.fnmatchcase(name, pattern)
