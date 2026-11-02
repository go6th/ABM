"""The read plan: the reading of a table question that code can do before the model starts (Ask
redesign phase 2, 2026-11-02).

Fable reads what a question needs; Sonnet and Haiku, left to call ``describe_table`` and
``query_table`` themselves, sometimes call nothing and answer blind (staging 2026-11-01:
``route=tabular tools_called=0``). So, for a tabular or mixed turn, code now does the reading
that needs no judgment:

1. **Which tables.** The tables whose columns or stored values the question names (the router's
   own lexicon, ``replica.router``); a workspace of at most two tables needs no matching. At most
   ``MAX_TABLES`` of them, the best match first.
2. **Describe them.** ``documents.tables.describe`` runs here and its result opens the user
   message, and the table counts as described for the turn's tools — the model queries at once.
3. **Read them whole, when they fit.** A table whose rows hold at most ``max_chars`` characters
   (``ASK_TABLE_IN_CONTEXT_MAX_CHARS``) is read in full — every row, every column, long text
   included — and rides in the cached file block beside the uploaded files, so a model that
   skips the tools still has every row. The tools stay: counts and filters over a sheet are still
   answered from the store's complete sets, and a table above the budget is only described.

Nothing here calls a model, and the same question over the same workspace plans the same reading.
Names and cell values go only into the model's prompt; they are never logged.

GUARANTEE(test_the_read_plan_reads_the_matched_tables_before_the_model_starts)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from account_intelligence.replica import router as R
from documents import repo
from documents import tables as T

MAX_TABLES = 3
ROW_CHARS = 2000  # a cell's text is cut here in the whole-table view (the store keeps it whole)


@dataclass
class ReadPlan:
    """What was read for a turn: the planned tables in order, their descriptions, and the whole
    text of those that fit."""

    tables: list[T.TableInfo] = field(default_factory=list)
    described: list[dict[str, Any]] = field(default_factory=list)
    whole: dict[Any, str] = field(default_factory=dict)  # table id -> rendered rows

    def pre_read(self) -> str:
        """The descriptions as the user message opens with them."""
        if not self.described:
            return ""
        import json

        return (
            "<pre_read>\nThese tables were already described for you (columns, types, sample"
            " values); query them directly.\n"
            + "\n".join(json.dumps(d, ensure_ascii=False, default=str) for d in self.described)
            + "\n</pre_read>"
        )


def pick_tables(question: str, tables: Sequence[T.TableInfo]) -> list[T.TableInfo]:
    """The tables the question is about (see the module docstring), best match first; ties keep
    the workspace's order (newest document first)."""
    if not tables:
        return []
    q = " ".join(question.casefold().split())
    scored: list[tuple[int, int, T.TableInfo]] = []
    for i, table in enumerate(tables):
        columns = R.column_hits(q, [table])
        values = R.value_hits(q, [table])
        score = len(columns) + len(values)
        if score:
            scored.append((-score, i, table))
    if scored:
        return [t for *_, t in sorted(scored, key=lambda x: (x[0], x[1]))][:MAX_TABLES]
    return list(tables) if len(tables) <= 2 else []


def render_whole(table: T.TableInfo, rows: Sequence[Any]) -> str:
    """A table's rows as the model reads them whole: a header of the normalised column names, then
    one line per row — its row number, then every cell, `` | `` between cells, line breaks inside
    a cell folded to spaces."""
    names = [c.name for c in table.columns]
    lines = [f"row | {' | '.join(names)}"]
    for r in rows:
        cells = r.cells or {}
        shown = []
        for name in names:
            value = cells.get(name)
            text = "" if value is None else " ".join(str(value).split())
            shown.append(text[:ROW_CHARS])
        lines.append(f"{int(r.row_no)} | {' | '.join(shown)}")
    return "\n".join(lines)


async def build(
    session: Any, question: str, tables: Sequence[T.TableInfo], *, max_chars: int
) -> ReadPlan:
    """The plan for this question over these tables (see the module docstring)."""
    plan = ReadPlan(tables=pick_tables(question, tables))
    held = 0
    for table in plan.tables:
        plan.described.append(await T.describe(session, table))
        if max_chars <= 0 or table.row_count <= 0:
            continue
        size = await repo.table_chars(session, table.id, view=table.view)
        if held + size > max_chars:
            continue
        rows = await repo.table_rows(
            session, table.id, "", {}, table.row_count + 1, view=table.view
        )
        plan.whole[table.id] = render_whole(table, rows)
        held += size
    return plan
