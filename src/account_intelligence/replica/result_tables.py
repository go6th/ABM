"""Answer tables built by code (Ask redesign 3b, 2026-11-02).

The model used to copy a query's rows into ``render_answer`` by hand, so a long table came out
shorter, reordered or reworded depending on the model. A ``result`` section names a query result
instead — ``{"kind": "result", "result_id": "q2", "columns": [...]}`` — and this module turns it
into the table: every row the query returned (the first ``CODE_TABLE_MAX_ROWS``), the row number
first, the chosen columns under their headers as written in the file, the citation of the table
last, and a note the code writes: how many rows matched of how many the table holds, the filters
as applied, duplicates removed, the spellings an alias filter added. A query that found nearby
rows (``documents.expanders``) gets a second table of them, nearest first, with the distance and
the place each was read as, and the count of rows whose place could not be read.

The model keeps what needs judgment: the headline, the numbers, the prose and callout, and which
columns the question touches. The citation is a placeholder, ``[T:<table id>]``, that
``model_answer`` turns into the table's numbered row.

GUARANTEE(test_result_sections_become_complete_tables_with_notes_and_nearby_rows)
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

CODE_TABLE_MAX_ROWS = 5000
MAX_COLUMNS = 10  # the row number and the citation make twelve
CELL_CHARS = 320
TITLE_CHARS = 80


_BRACKETED = re.compile(r"\[(\d{1,3})\]")


def cell(value: Any) -> str:
    """A stored cell as table text: lists joined, booleans Yes and No, blanks empty, spaces
    collapsed, cut at ``CELL_CHARS``. A number in square brackets in a cell becomes round
    brackets: the client reads ``[2]`` as a citation."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    text = _BRACKETED.sub(r"(\1)", " ".join(str(value).split()))
    return text if len(text) <= CELL_CHARS else text[: CELL_CHARS - 1].rstrip() + "…"


def _filters(out: Mapping[str, Any]) -> str:
    parts = []
    for w in out.get("applied_where", []):
        parts.append(f"{w['column']} {w['op']} {w['value']}")
    return "; ".join(parts)


def _note(
    out: Mapping[str, Any],
    table: Any,
    shown: int,
    extra: str | None,
    *,
    omitted: Sequence[str] = (),
    left_out: Sequence[int] = (),
) -> str:
    bits = [
        f"{out['row_count']} of {table.row_count} rows in {table.name} match"
        + (f" ({_filters(out)})" if _filters(out) else "")
        + "."
    ]
    if out.get("truncated") or shown < out["row_count"]:
        bits.append(f"Showing the first {shown}; the rest are one narrower query away.")
    if out.get("partial_rows"):
        bits.append(
            f"{out['partial_rows']} rows matched only as part of a longer value and are marked"
            " (partial)."
        )
    if left_out:
        bits.append(f"Left out at the answer's request: {len(left_out)} rows.")
    if omitted:
        bits.append(
            f"Not shown ({len(omitted)} of the result's columns): {', '.join(omitted)}; name the"
            " columns wanted to see them."
        )
    if out.get("dedup_removed"):
        bits.append(f"{out['dedup_removed']} duplicates removed on {out.get('deduped_on')}.")
    for e in out.get("expanded", []):
        bits.append(f"{e['term']} was also matched as {', '.join(e['also'])}.")
    related = out.get("related")
    if related and related.get("skipped"):
        bits.append(f"The nearby search was skipped ({related['skipped']}).")
    if extra:
        bits.append(extra)
    return " ".join(bits)


def build(
    section: Mapping[str, Any],
    stored: Mapping[str, tuple[Any, Any, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], tuple[str, str] | None]:
    """The table sections a ``result`` section stands for, or a (code, message) error the model
    can fix on its next round."""
    rid = str(section.get("result_id"))
    if rid not in stored:
        known = ", ".join(sorted(stored)) or "none yet"
        return [], ("unknown_result", f"No query result {rid!r}; the results are: {known}")
    table, query, out = stored[rid]
    available = [
        c.name for c in table.columns if c.name in (out["rows"][0]["cells"] if out["rows"] else {})
    ]
    if not available:
        available = list(query.columns) or [c.name for c in table.columns if not c.payload]
    asked = [str(c) for c in section.get("columns") or ()]
    names = table.names
    chosen = []
    for name in asked:
        key = name if name in names else name.casefold().replace(" ", "_")
        if key not in available:
            return [], (
                "bad_columns",
                f"{name!r} is not a column of result {rid}; its columns are:"
                f" {', '.join(available)}",
            )
        chosen.append(key)
    wanted = chosen or available
    chosen = wanted[:MAX_COLUMNS]
    omitted = [names[c].raw if c in names else c for c in wanted[MAX_COLUMNS:]]
    headers = [names[c].raw if c in names else c for c in chosen]
    cite = f"[T:{table.id}]"
    left_out = {int(n) for n in section.get("exclude_rows") or ()}
    rows = [r for r in out["rows"] if int(r["row"]) not in left_out][:CODE_TABLE_MAX_ROWS]
    main = {
        "kind": "table",
        "title": str(section.get("title") or f"{table.name}: {out['row_count']} matching rows")[
            :TITLE_CHARS
        ],
        "columns": ["Row", *headers, "Source"],
        "rows": [
            [
                f"{r['row']} (partial)" if r.get("partial") else str(r["row"]),
                *(cell(r["cells"].get(c)) for c in chosen),
                cite,
            ]
            for r in rows
        ],
        "note": _note(
            out,
            table,
            len(rows),
            section.get("note"),
            omitted=omitted,
            left_out=sorted(left_out & {int(r["row"]) for r in out["rows"]}),
        ),
    }
    sections = [main]
    related = out.get("related")
    if related and related.get("rows"):
        sections.append(
            {
                "kind": "table",
                "title": f"Nearby: within {related['radius_miles']:g} miles of {related['target']}"[
                    :TITLE_CHARS
                ],
                "columns": ["Row", *headers, "Distance (mi)", "Read as", "Source"],
                "rows": [
                    [
                        str(r["row"]),
                        *(cell(r["cells"].get(c)) for c in chosen),
                        f"{r['distance_miles']:g}",
                        r["place"],
                        cite,
                    ]
                    for r in related["rows"][:CODE_TABLE_MAX_ROWS]
                ],
                "note": (
                    f"{related['count']} more rows lie within {related['radius_miles']:g} miles"
                    f" of {related['target']} (nearest first); {related['unplaced']} other rows"
                    " have no location that could be placed."
                ),
            }
        )
    return sections, None


def expand(
    payload: dict[str, Any], stored: Mapping[str, tuple[Any, Any, dict[str, Any]]]
) -> tuple[dict[str, Any], tuple[str, str] | None]:
    """The payload with every ``result`` section replaced by its tables; the payload itself when
    it has none."""
    if not any(
        isinstance(s, dict) and s.get("kind") == "result" for s in payload.get("sections", ())
    ):
        return payload, None
    sections: list[dict[str, Any]] = []
    for section in payload["sections"]:
        if isinstance(section, dict) and section.get("kind") == "result":
            built, error = build(section, stored)
            if error is not None:
                return payload, error
            sections.extend(built)
        else:
            sections.append(section)
    return {**payload, "sections": sections}, None
