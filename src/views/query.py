"""Server-side, bounded table queries (VW3).

A query is a ``QuerySpec`` (filters, sorts, a search phrase, a page size and a cursor), never SQL.
``validate`` checks it against the table spec: only the table's columns, only each column kind's
operators, bounded counts and lengths. ``compile`` turns a validated query into ONE parameterised
statement over ``view_rows`` inside the tenant's transaction: column keys are interpolated only
after they matched the spec (fixed identifiers), and every value is a bound parameter. Search is
full text in SQL (``search_doc @@ plainto_tsquery``), applied before rows are returned, never
after. Pagination is keyset on (sort values, position); a cursor is an opaque, validated token.

GUARANTEE(test_query_validation_refuses_everything_outside_the_allow_lists)
GUARANTEE(test_queries_match_a_hand_written_oracle)
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from account_intelligence.config import CONFIG_DIR
from views.tables import Op, TableSpec

_KEY = re.compile(r"^[a-z_]{1,40}$")
Value = str | int | list[str] | None


class QueryError(ValueError):
    """A query outside the allow-lists; ``code`` is a fixed reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class QueryLimits:
    """``config/views.yaml`` query limits."""

    default_page_size: int
    max_page_size: int
    max_filters: int
    max_sorts: int
    max_in_values: int
    max_value_chars: int
    max_search_chars: int


@lru_cache
def load_limits() -> QueryLimits:
    """The query limits from configuration (read once)."""
    raw = yaml.safe_load((CONFIG_DIR / "views.yaml").read_text())["query"]
    return QueryLimits(**{k: int(v) for k, v in raw.items()})


class Filter(BaseModel):
    """One filter clause."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    column: str
    op: Op
    value: Value = None


class Sort(BaseModel):
    """One sort key."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    column: str
    direction: Literal["asc", "desc"] = "asc"


class QuerySpec(BaseModel):
    """What a client may ask of a table (also what a saved view stores)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    filters: tuple[Filter, ...] = ()
    sort: tuple[Sort, ...] = ()
    search: str | None = None
    page_size: int | None = Field(default=None, ge=1)


@dataclass(frozen=True)
class Validated:
    """A query that passed the allow-lists."""

    spec: QuerySpec
    table: TableSpec
    page_size: int


def parse(raw: dict[str, Any]) -> QuerySpec:
    """A spec from client JSON; QueryError on any shape error."""
    try:
        return QuerySpec.model_validate(raw)
    except ValidationError:
        raise QueryError("malformed_query") from None


def validate(spec: QuerySpec, table: TableSpec, limits: QueryLimits | None = None) -> Validated:
    """The spec checked against the table's columns and operators and the limits."""
    lim = limits or load_limits()
    if len(spec.filters) > lim.max_filters:
        raise QueryError("too_many_filters")
    if len(spec.sort) > lim.max_sorts:
        raise QueryError("too_many_sorts")
    if spec.search is not None and len(spec.search) > lim.max_search_chars:
        raise QueryError("search_too_long")
    page = spec.page_size or lim.default_page_size
    if page > lim.max_page_size:
        raise QueryError("page_too_large")
    for f in spec.filters:
        col = table.column(f.column)
        if col is None or not _KEY.match(f.column):
            raise QueryError("unknown_column")
        if f.op not in col.ops:
            raise QueryError("operator_not_allowed")
        _check_value(f, col.kind, lim)
    seen: set[str] = set()
    for s in spec.sort:
        col = table.column(s.column)
        if col is None or not col.sortable or not _KEY.match(s.column):
            raise QueryError("unsortable_column")
        if s.column in seen:
            raise QueryError("duplicate_sort")
        seen.add(s.column)
    return Validated(spec, table, page)


def _check_value(f: Filter, kind: str, lim: QueryLimits) -> None:
    if f.op in ("is_empty", "not_empty"):
        if f.value is not None:
            raise QueryError("value_not_allowed")
        return
    if f.op == "in":
        if not isinstance(f.value, list) or not 1 <= len(f.value) <= lim.max_in_values:
            raise QueryError("bad_in_values")
        if any(not isinstance(v, str) or len(v) > lim.max_value_chars for v in f.value):
            raise QueryError("bad_in_values")
        return
    if kind == "count":
        if not isinstance(f.value, int) or isinstance(f.value, bool):
            raise QueryError("count_needs_integer")
        return
    if not isinstance(f.value, str) or not f.value or len(f.value) > lim.max_value_chars:
        raise QueryError("bad_value")
    if kind == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.value):
        raise QueryError("date_needs_iso")


def _expr(key: str, kind: str) -> str:
    """The SQL expression of one validated column key (never a client string)."""
    if not _KEY.match(key):
        raise QueryError("unknown_column")
    if kind == "count":
        return f"(NULLIF(cells->>'{key}', '')::numeric)"
    return f"coalesce(cells->>'{key}', '')"


def encode_cursor(values: list[Any], position: int) -> str:
    """An opaque cursor for keyset pagination."""
    body = json.dumps([values, position], separators=(",", ":"), default=str)
    return base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, n_sorts: int) -> tuple[list[Any], int]:
    """A cursor back to (sort values, position); QueryError when it is not one of ours."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        values, position = json.loads(raw)
    except (ValueError, TypeError):
        raise QueryError("bad_cursor") from None
    if (
        not isinstance(values, list)
        or len(values) != n_sorts
        or not isinstance(position, int)
        or isinstance(position, bool)
        or position < 0
    ):
        raise QueryError("bad_cursor")
    if any(not isinstance(v, str | int | float) or isinstance(v, bool) for v in values):
        raise QueryError("bad_cursor")
    return values, position


def compile_query(
    q: Validated, *, run_id: str, cursor: str | None = None
) -> tuple[str, dict[str, Any]]:
    """One parameterised statement returning page_size + 1 rows (the extra row says whether
    there is a next page). Runs under the caller's tenant transaction (RLS)."""
    params: dict[str, Any] = {"run": run_id, "kind": q.table.kind, "n": q.page_size + 1}
    where = ["run_id = CAST(:run AS uuid)", "table_kind = :kind"]
    for i, f in enumerate(q.spec.filters):
        col = q.table.column(f.column)
        assert col is not None
        e, p = _expr(f.column, col.kind), f"f{i}"
        if f.op == "eq":
            where.append(f"{e} = :{p}")
        elif f.op == "ne":
            where.append(f"{e} <> :{p}")
        elif f.op == "contains":
            where.append(f"{e} ILIKE '%' || :{p} || '%' ESCAPE '\\'")
        elif f.op == "starts_with":
            where.append(f"{e} ILIKE :{p} || '%' ESCAPE '\\'")
        elif f.op == "in":
            where.append(f"{e} = ANY(:{p})")
        elif f.op == "gte":
            where.append(f"{e} >= :{p}")
        elif f.op == "lte":
            where.append(f"{e} <= :{p}")
        elif f.op == "is_empty":
            where.append(f"{e} = ''" if col.kind != "count" else f"{e} IS NULL")
            continue
        else:  # not_empty
            where.append(f"{e} <> ''" if col.kind != "count" else f"{e} IS NOT NULL")
            continue
        value = f.value
        if f.op in ("contains", "starts_with") and isinstance(value, str):
            value = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params[p] = value
    if q.spec.search:
        where.append("search_doc @@ plainto_tsquery('simple', :q)")
        params["q"] = q.spec.search
    order_exprs: list[tuple[str, str]] = []
    for s in q.spec.sort:
        col = q.table.column(s.column)
        assert col is not None
        order_exprs.append((_expr(s.column, col.kind), s.direction))
    if cursor is not None:
        values, position = decode_cursor(cursor, len(order_exprs))
        params["cpos"] = position
        clauses = []
        for i in range(len(order_exprs) + 1):
            parts = []
            for j in range(i):
                params[f"c{j}"] = values[j]
                parts.append(f"{order_exprs[j][0]} = :c{j}")
            if i < len(order_exprs):
                e, direction = order_exprs[i]
                params[f"c{i}"] = values[i]
                parts.append(f"{e} {'>' if direction == 'asc' else '<'} :c{i}")
            else:
                parts.append("position > :cpos")
            clauses.append("(" + " AND ".join(parts) + ")")
        where.append("(" + " OR ".join(clauses) + ")")
    order = ", ".join(f"{e} {d.upper()}" for e, d in order_exprs)
    order = f"{order}, position ASC" if order else "position ASC"
    select = ", ".join(
        [f"{e} AS s{i}" for i, (e, _) in enumerate(order_exprs)]
        + ["position", "ref", "row_id", "cells", "source_code", "source_url", "source_state"]
    )
    sql = f"SELECT {select} FROM view_rows WHERE {' AND '.join(where)} ORDER BY {order} LIMIT :n"
    return sql, params


def next_cursor(rows: list[Any], q: Validated) -> str | None:
    """The cursor after the last row of a full page, or None at the end."""
    if len(rows) <= q.page_size:
        return None
    last = rows[q.page_size - 1]
    values = [_json_value(getattr(last, f"s{i}")) for i in range(len(q.spec.sort))]
    return encode_cursor(values, int(last.position))


def _json_value(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, int | float | str):
        return v
    return float(v) if "." in str(v) else int(v)
