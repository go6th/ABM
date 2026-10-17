"""The data tools as one Ask turn runs them (2026-10-17): ``list_sources``, ``describe_table``,
``query_table`` and ``read_rows`` over the table store, ``search_passages``, ``read_passage``
and ``read_span`` over the passages, and — only for a tenant whose ``run_python`` flag is on —
``run_python`` over the turn's results in the sandbox (``documents.run_python``), bound to the
caller's tenant and accounts, with the turn's statistics for the ``ask.route`` log line (tools
called, predicates, the last row count, the predicate the model relaxed, rows removed by
deduplication, empty results, sandbox runs). Every route also offers ``render_answer``: the
final answer as strict JSON (``replica.render``), shape-checked here and then the tool loop
ends — the answer's format is the code's, not the model's.

Which are on offer depends on the turn's route (the brief's exposure rule): ``tabular`` gets
``list_sources`` and the table tools only, ``prose`` ``list_sources`` and the passage tools,
``mixed`` all — each with ``render_answer`` on top. Every call opens its own short tenant transaction (the answer streams outside
one); the stores (``documents.tables``, ``documents.sources``) check columns and operators and
bind every value, and an error comes back to the model as a structured result, never as an
exception. Querying or reading a table the turn has not described is refused the same way, so
the model always reads the columns and types first. Predicates are logged as ``column:op``
only (no values: they are the user's words).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

from documents import run_python as RP
from documents import sources as S
from documents import tables as T
from obs.logging import logger

from account_intelligence.replica import render as RENDER

log = logger(__name__)

TxnFactory = Callable[[], AbstractAsyncContextManager[Any]]

# which tools a route offers (Part E): tabular questions are computed from the store, prose
# ones read the passages, mixed have both; list_sources is everywhere. Every route with tools
# also offers render_answer (2026-10-17): the final answer as strict JSON, rendered by the
# code, so every model formats rows and tables identically (see replica.render)
TABULAR_TOOLS = ("describe_table", "query_table", "read_rows")
PROSE_TOOLS = ("search_passages", "read_passage", "read_span")
ROUTE_TOOLS = {
    "tabular": ("list_sources", *TABULAR_TOOLS, RENDER.NAME),
    "prose": ("list_sources", *PROSE_TOOLS, RENDER.NAME),
    "mixed": ("list_sources", *TABULAR_TOOLS, *PROSE_TOOLS, RENDER.NAME),
}


class TableTools:
    """See the module docstring."""

    def __init__(self, txn: TxnFactory, tables: Sequence[T.TableInfo]) -> None:
        self.txn = txn
        self.tables = list(tables)
        self.described: set[UUID] = set()
        self.calls: list[dict[str, Any]] = []
        self.last_where: dict[UUID, set[tuple[str, str]]] = {}
        self.relaxed: str | None = None
        self.row_count: int | None = None
        self.dedup_removed = 0
        self.empty_results = 0

    @property
    def tools(self) -> tuple[dict[str, Any], ...]:
        """The tool definitions the model is offered."""
        return T.TOOLS

    def stats(self) -> dict[str, Any]:
        """The turn's statistics for the log line (values never include the user's words)."""
        return {
            "tools_called": len(self.calls),
            "predicates": ",".join(
                f"{p['column']}:{p['op']}"
                for c in self.calls
                if c["name"] == "query_table"
                for p in c.get("applied_where", [])
            )[:200],
            "row_count": self.row_count,
            "relaxed_predicate": self.relaxed,
            "dedup_removed": self.dedup_removed,
            "empty_results": self.empty_results,
        }

    async def run(self, name: str, payload: dict[str, Any]) -> Any:
        """Run one tool call; the result (or the structured error) the model reads."""
        record: dict[str, Any] = {"name": name}
        self.calls.append(record)
        try:
            if name == "describe_table":
                table = T.find_table(self.tables, str(payload.get("table", "")))
                record["table"] = table.name
                async with self.txn() as s:
                    out = await T.describe(s, table)
                self.described.add(table.id)
                return out
            if name == "query_table":
                table = T.find_table(self.tables, str(payload.get("table", "")))
                record["table"] = table.name
                if table.id not in self.described:
                    raise T.QueryError(
                        "describe_first",
                        f"call describe_table on {table.name!r} before querying it",
                    )
                query = T.parse(table, payload)
                record["applied_where"] = query.applied_where()
                async with self.txn() as s:
                    out = await T.run(s, query)
                self._note(table.id, query, out)
                return out
            if name == "read_rows":
                table = T.find_table(self.tables, str(payload.get("table", "")))
                record["table"] = table.name
                if table.id not in self.described:
                    raise T.QueryError(
                        "describe_first",
                        f"call describe_table on {table.name!r} before reading its rows",
                    )
                row_nos, columns = T.parse_read(table, payload)
                async with self.txn() as s:
                    return await T.read(s, table, row_nos, columns)
            raise T.QueryError("unknown_tool", f"no tool named {name!r}")
        except T.QueryError as error:
            record["error"] = error.code
            return error.payload()

    def _note(self, table_id: UUID, query: T.Query, out: dict[str, Any]) -> None:
        now = {(w.column.name, w.op) for w in query.where}
        before = self.last_where.get(table_id)
        if before is not None and now < before and len(before - now) == 1:
            ((column, _),) = before - now
            self.relaxed = column
        self.last_where[table_id] = now
        self.row_count = int(out.get("row_count", 0))
        self.dedup_removed += int(out.get("dedup_removed", 0) or 0)
        if not out.get("row_count"):
            self.empty_results += 1


class AccessTools:
    """All six harness tools of one Ask turn, with exposure by route (see the module
    docstring). The table calls and their statistics stay in :class:`TableTools`. ``run_python``
    (``documents.run_python``) joins the offer on the tabular and mixed routes only when the
    tenant's ``run_python`` flag is on, and runs the turn's results so far as the code's
    ``data``."""

    def __init__(
        self,
        txn: TxnFactory,
        tables: Sequence[T.TableInfo],
        accounts: Sequence[UUID],
        embedder: Any | None = None,
        run_python: bool = False,
    ) -> None:
        self.txn = txn
        self.accounts = list(accounts)
        self.embedder = embedder
        self.run_python = run_python
        self.table_tools = TableTools(txn, tables)
        self.searches = 0
        self.python_calls = 0
        self.results: list[Any] = []
        self.rendered: dict[str, Any] | None = None  # the render_answer call, if one came
        self._all: dict[str, dict[str, Any]] = {
            t["name"]: t for t in (*T.TOOLS, *S.SOURCE_TOOLS, RENDER.RENDER_TOOL)
        }

    def offered(self, route: str) -> tuple[dict[str, Any], ...]:
        """The tool definitions this route's model is offered."""
        names = list(ROUTE_TOOLS.get(route, ROUTE_TOOLS["prose"]))
        if self.run_python and route in ("tabular", "mixed"):
            names.append("run_python")
        offered = dict(self._all)
        offered["run_python"] = RP.RUN_PYTHON_TOOL
        return tuple(offered[name] for name in names)

    @property
    def calls(self) -> list[dict[str, Any]]:
        """Every tool call this turn ran, in order (name, table, any error code)."""
        return self.table_tools.calls

    def stats(self) -> dict[str, Any]:
        """The turn's statistics (the table tools', plus the searches and sandbox runs)."""
        return {
            **self.table_tools.stats(),
            "searches": self.searches,
            "run_python_used": self.python_calls,
        }

    async def run(self, name: str, payload: dict[str, Any]) -> Any:
        """Run one tool call; the result (or the structured error) the model reads. Every
        result joins the turn's results, which are a later ``run_python`` code's ``data``.
        ``render_answer`` is shape-checked here and ends the tool loop (``terminal``); its
        citations are checked downstream, by the code that numbered the workspace rows."""
        try:
            if name == RENDER.NAME:
                out = self._render(payload)
            elif name in TABULAR_TOOLS:
                out = await self.table_tools.run(name, payload)
            elif name == "run_python":
                out = await self._run_python(payload)
            else:
                out = await self._prose(name, payload)
        except T.QueryError as error:
            out = error.payload()
        self.results.append(out)
        return out

    def _render(self, payload: dict[str, Any]) -> Any:
        """A render_answer call: shape-checked (replica.render), then terminal — the tool loop
        ends and the call's input becomes the answer."""
        error = RENDER.check_shape(payload)
        if error is not None:
            code, message = error
            return {"error": {"code": code, "message": message}}
        self.rendered = payload
        return {"ok": True, "terminal": True}

    async def _run_python(self, payload: dict[str, Any]) -> Any:
        """One sandbox run: refused unless the tenant's flag was on for this turn; logged by
        the code's sha256 (never the code)."""
        if not self.run_python:
            raise T.QueryError("disabled", "run_python is not on for this workspace")
        from account_intelligence.replica import obs as ask_obs

        hashed = RP.hash_code(payload["code"]) if isinstance(payload.get("code"), str) else ""
        self.python_calls += 1
        try:
            out = await RP.run(payload.get("code"), self.results)
        except T.QueryError as error:
            log.info("ask.run_python", extra={"code_sha256": hashed, "outcome": error.code})
            ask_obs.record_run_python("error")
            raise
        log.info("ask.run_python", extra={"code_sha256": hashed, "outcome": "ok"})
        ask_obs.record_run_python("ok")
        return out

    async def _prose(self, name: str, payload: dict[str, Any]) -> Any:
        """One list_sources or passage call (see the module docstring)."""
        if name == "list_sources":
            async with self.txn() as s:
                return await S.list_sources(s, self.accounts)
        if name == "search_passages":
            top_k = payload.get("top_k", 10)
            if (
                not isinstance(top_k, int)
                or isinstance(top_k, bool)
                or not 1 <= top_k <= S.SEARCH_TOP_K_MAX
            ):
                raise T.QueryError(
                    "bad_top_k", f"top_k must be an integer between 1 and {S.SEARCH_TOP_K_MAX}"
                )
            query = str(payload.get("query") or "").strip()
            if not query:
                raise T.QueryError("bad_query", "a search needs a query")
            filters = S.parse_filters(payload)
            self.searches += 1
            async with self.txn() as s:
                return await S.search(s, query, self.accounts, self.embedder, top_k, filters)
        if name == "read_passage":
            async with self.txn() as s:
                return await S.read_passage(s, str(payload.get("chunk_id") or ""), self.accounts)
        if name == "read_span":
            async with self.txn() as s:
                return await S.read_span(
                    s,
                    str(payload.get("from_chunk") or ""),
                    str(payload.get("to_chunk") or ""),
                    self.accounts,
                )
        raise T.QueryError("unknown_tool", f"no tool named {name!r}")
