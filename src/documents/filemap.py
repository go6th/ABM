"""The workspace file map (Ask redesign phase 1, 2026-11-02): one compact manifest of every file
the caller uploaded — each sheet, its row count, its key and EVERY column with its type and, for
a column with few values, those values — that opens every Ask turn.

Why: a model that is not told what exists decides for itself what to open. Sonnet and Haiku
(unlike Fable) answered a tabular question with no tool call at all (staging, 2026-11-01:
``route=tabular tools_called=0``), so the map names every table and column up front and says
how each is read. It is built only from what ingest already stored (``document_tables.columns``,
the row counts, the low-cardinality values, ``documents.sources.list_sources``): a pure function,
no model call, no extra query beyond ``list_sources``, the same text for the same workspace (so
it caches). It carries names, types and values the caller's own files hold, goes only into the
model's system prompt and is never logged.

Size: tables and columns are never dropped. When the map exceeds ``MAP_MAX_CHARS`` the value
lists go first, then the header spellings; what remains is returned whole.

GUARANTEE(test_the_file_map_names_every_table_and_every_column)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from documents.tables import ColumnInfo, TableInfo

MAP_MAX_CHARS = 24_000
VALUES_SHOWN = 12
VALUE_CHARS = 40

HEADER = (
    "Every file in this workspace is listed below with every sheet and every column. The rows of"
    " each table are stored COMPLETE; this list is only the index. When you are given the table"
    " tools, read a table with `describe_table` then `query_table` (a payload column:"
    " `read_rows`) and answer from the result, never from this index or from memory. Never say"
    " a file, sheet or column is missing, and never ask for a re-upload."
)


def _values(column: ColumnInfo) -> str:
    shown = [v[:VALUE_CHARS] for v in column.values[:VALUES_SHOWN]]
    more = len(column.values) - len(shown)
    return "values: " + " | ".join(shown) + (f" | +{more} more" if more > 0 else "")


def _column(column: ColumnInfo, *, values: bool, header: bool) -> str:
    notes = [column.type]
    if column.payload:
        notes.append("long text: read_rows")
    if column.multi_valued:
        notes.append("many per cell")
    if values and column.values and not column.payload:
        notes.append(_values(column))
    name = column.name
    if header and column.raw and column.raw != column.name:
        name += f' (header "{column.raw[:60]}")'
    return f"{name} [{'; '.join(notes)}]"


def _table(t: TableInfo, *, values: bool, header: bool) -> str:
    where = t.document + (f", {t.member}" if t.member else "")
    key = f"; key: {t.business_key}" if t.business_key else ""
    head = f'TABLE "{t.name}" — {where} — {t.row_count} rows, {len(t.columns)} columns{key}'
    lines = [head]
    lines += [f"  - {_column(c, values=values, header=header)}" for c in t.columns]
    return "\n".join(lines)


def _prose(sources: Sequence[Mapping[str, Any]]) -> list[str]:
    out = []
    for s in sources:
        if s.get("shape") == "tabular":
            continue  # its table is listed above
        what = str(s.get("shape") or "prose")
        size = f"{s['pages']} pages" if s.get("pages") else f"{s.get('passages', 0)} passages"
        member = f", {s['member']}" if s.get("member") else ""
        out.append(f"DOCUMENT ({what}) — {s.get('document', 'a file')}{member} — {size}")
    return out


SKIPPED_SHOWN = 15
# what a document in each unfinished or failed status means for an answer
STATES = {
    "awaiting_upload": "the upload has not finished",
    "quarantined": "waiting for the malware scan",
    "scanning": "being scanned",
    "parsing": "still being read",
    "parsed": "read, still being indexed",
    "indexing": "still being indexed",
    "rejected": "rejected and not read",
    "failed": "could not be read",
}


COVERAGE_RULE = (
    "FILE COVERAGE — what was and was not read. When your answer rests on a file listed here as"
    ' partly read, skipped, unfinished or failed, say so in one line (for example: "117 of 120'
    ' files read; 3 skipped"); never present an unread file as read.'
)


def _coverage(entries: Sequence[Mapping[str, Any]]) -> list[str]:
    """The coverage lines: every archive (read, skipped by name, read without text) and every file
    that is unfinished or failed; a plain file that was read in full needs no line."""
    lines = []
    for e in entries:
        name = f'"{e.get("document", "a file")}"'
        status = str(e.get("status") or "")
        if status in STATES:
            why = f": {e['why']}" if e.get("why") else ""
            lines.append(f"- {name}: {STATES[status]}{why}")
            continue
        if "members_read" not in e:
            if e.get("parts_failed"):
                lines.append(f"- {name}: {e['parts_failed']} part(s) could not be read")
            continue
        skipped = list(e.get("members_skipped") or [])
        bits = [f"{e['members_read']} files read"]
        if skipped:
            shown = "; ".join(f"{m['member']}: {m['reason']}" for m in skipped[:SKIPPED_SHOWN])
            more = len(skipped) - SKIPPED_SHOWN
            bits.append(f"{len(skipped)} skipped ({shown}{f'; +{more} more' if more > 0 else ''})")
        if e.get("members_no_text"):
            bits.append(f"{e['members_no_text']} read but held no text (scanned images or empty)")
        if e.get("parts_failed"):
            bits.append(f"{e['parts_failed']} part(s) could not be read")
        lines.append(f"- ARCHIVE {name}: " + ", ".join(bits))
    return lines


def render(
    tables: Sequence[TableInfo],
    sources: Sequence[Mapping[str, Any]] = (),
    coverage: Sequence[Mapping[str, Any]] = (),
    *,
    max_chars: int = MAP_MAX_CHARS,
) -> str:
    """The map as the model reads it, or "" for a workspace with no file."""
    prose = _prose(sources)
    covered = _coverage(coverage)
    if not tables and not prose and not covered:
        return ""
    # most detail first; each step down drops one kind of detail, never a table or a column
    for values, header in ((True, True), (False, True), (False, False)):
        body = [_table(t, values=values, header=header) for t in tables] + prose
        head = ["<workspace_files>", HEADER, f"{len(tables)} table(s) in total."]
        text = (
            "\n".join([*head, *body, *([COVERAGE_RULE, *covered] if covered else [])])
            + "\n</workspace_files>"
        )
        if len(text) <= max_chars:
            break
    return text
