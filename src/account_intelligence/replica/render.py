"""``render_answer``: the model's final answer as strict JSON instead of free Markdown
(owner decision 2026-10-17 — the structured-answer phase of the tabular brief).

A turn that offers data tools (every route: tabular, prose, mixed — replica.tools) also
offers this one, and the model is asked to end with exactly one call carrying the whole
answer: a headline, a few headline numbers and sections in reading order — and every answer
carries at least one table (every matching row, or for a narrative answer the facts, rows or
figures it rests on; prose is at most two sentences, one callout, next steps). The format of
the answer is then the code's to produce, so Kimi K3 and the Claude models render
identically: the brief's rows-and-table mandate can no longer be a habit one model has and
another does not.

Two checks run on a call, in two places that each own half the knowledge:

* :func:`check_shape` — the access tools, which see no workspace rows, verify the JSON shape
  (kinds, sizes, cell counts). A shape error goes back to the model as a result it can fix
  on the next round; a good call ends the tool loop (the result carries ``terminal``).
* :func:`rewrite` — ``model_answer`` owns the numbered rows and checks every ``[R3]``
  citation in the payload against the rows that were sent, rewriting them to row-key
  markers, exactly as ``checked`` does for Markdown. An unknown id discards the reply; the
  local answer stands.

The validated payload rides ``Draft.structured`` to ``answers.finish``, which numbers its
citations like any other and hands it to the client beside the same answer as Markdown
paragraphs (the stream, the stored plain text, the follow-ups all come from the draft).
"""

from __future__ import annotations

import re
from typing import Any

from account_intelligence.replica.answers import Draft, Row
from obs.logging import logger

log = logger("replica.render")

NAME = "render_answer"

# sizes: generous enough for a complete table of every matching row (the whole point),
# bounded enough that a runaway call is still one call
MAX_HEADLINE = 240
MAX_STATS = 6
MAX_SECTIONS = 12
MAX_TABLE_ROWS = 400
MAX_PROSE_PARAGRAPHS = 3
MAX_PROSE_SENTENCES = 2
PROSE_MAX_CHARS = 480
CELL_MAX_CHARS = 320
MAX_STEPS = 8
MAX_TITLE = 80

TONES = ("info", "warn", "good")

_str = {"type": "string", "maxLength": CELL_MAX_CHARS}
RENDER_TOOL: dict[str, Any] = {
    "name": NAME,
    "description": (
        "Render the final answer. Call ONCE, at the very end, after every data tool result "
        "you need: the call IS the answer — a one-sentence headline, up to 6 headline "
        "numbers, and sections in reading order. Every answer includes at least one table "
        "section: it carries EVERY matching row (all of them, never a sample, never "
        "'and others'), one cell per column, each row's citations ([R3]) in its last cell; "
        "a narrative answer still adds a table of the facts, rows or figures it rests on. "
        "A prose section is at most two sentences per paragraph. A callout holds one "
        "warning or key finding. Steps list what the seller does next. Cite the numbered "
        "workspace rows as [R3] inside the strings."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["headline", "sections"],
        "properties": {
            "headline": {"type": "string", "maxLength": MAX_HEADLINE},
            "stats": {
                "type": "array",
                "maxItems": MAX_STATS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["label", "value"],
                    "properties": {
                        "label": {"type": "string", "maxLength": 48},
                        "value": {"type": "string", "maxLength": 48},
                    },
                },
            },
            "sections": {
                "type": "array",
                "maxItems": MAX_SECTIONS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind"],
                    "properties": {
                        "kind": {"enum": ["table", "prose", "callout", "steps"]},
                        "title": {"type": "string", "maxLength": MAX_TITLE},
                        "columns": {"type": "array", "maxItems": 12, "items": _str},
                        "rows": {
                            "type": "array",
                            "maxItems": MAX_TABLE_ROWS,
                            "items": {"type": "array", "maxItems": 12, "items": _str},
                        },
                        "note": _str,
                        "paragraphs": {
                            "type": "array",
                            "maxItems": MAX_PROSE_PARAGRAPHS,
                            "items": _str,
                        },
                        "text": _str,
                        "tone": {"enum": list(TONES)},
                        "items": {"type": "array", "maxItems": MAX_STEPS, "items": _str},
                    },
                },
            },
        },
    },
}

# the prompt rules a turn gets when the tool is on offer, after SYSTEM (and TABULAR_RULES on
# a table route): what the answer becomes when rows are involved
RENDER_RULES = (
    "End your turn with exactly ONE `render_answer` call carrying the whole answer as strict"
    " JSON; call it after your last data tool result and write nothing after it. `headline`:"
    " one sentence answering the question directly. `stats`: the 0-6 numbers the headline"
    " rests on. `sections` in reading order, and EVERY answer carries at least one `table`"
    " section: it holds EVERY matching row — all of them, never a sample or the highlights —"
    " one cell per column the question touches, each row's citations ([R3]) in its last"
    " cell. When the answer is narrative, the table still shows the facts, rows or figures"
    " the answer rests on (one fact per row). The other kinds: a `prose` section is at most"
    " two sentences per paragraph; a `callout` holds one warning or key finding; `steps`"
    " lists what the seller does next. Cite the numbered rows as [R3] inside the strings, as"
    " rule 1 says. A call without a table section comes back as an error you can fix."
)

_CITE = re.compile(r"\[(R\d{1,3})\]")
# approximate sentence count: punctuation followed by a space or the end (a citation [R3]
# after the period does not start a new sentence)
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")


def check_shape(payload: Any) -> tuple[str, str] | None:
    """Shape-only validation of one call's input; an (code, message) error the model can fix
    on the next round, or None when the shape is good. Citations are checked later, by the
    code that knows the numbered rows (rewrite), so a shape-good call can still be unusable."""
    if not isinstance(payload, dict):
        return "bad_shape", "the answer must be a JSON object"
    unknown = set(payload) - {"headline", "stats", "sections"}
    if unknown:
        return "bad_shape", f"unknown fields: {sorted(unknown)}"
    headline = payload.get("headline")
    if not isinstance(headline, str) or not headline.strip():
        return "bad_headline", "headline must be a non-empty string"
    if len(headline) > MAX_HEADLINE:
        return "bad_headline", f"headline is longer than {MAX_HEADLINE} characters"
    error = _check_stats(payload.get("stats"))
    if error:
        return error
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        return "bad_sections", "sections must be a non-empty array"
    if len(sections) > MAX_SECTIONS:
        return "bad_sections", f"at most {MAX_SECTIONS} sections"
    for i, section in enumerate(sections):
        error = _check_section(i, section)
        if error:
            return error
    # the owner mandate (2026-10-17): every answer builds a table — narrative answers
    # included; a call without one is an error the model can fix on the next round
    if not any(isinstance(s, dict) and s.get("kind") == "table" for s in sections):
        return (
            "no_table",
            "every answer carries at least one table section: every matching row, or — for a"
            " narrative answer — a table of the facts, rows or figures it rests on",
        )
    return None


def _string_list(value: Any, limit: int) -> bool:
    return (
        isinstance(value, list)
        and 0 < len(value) <= limit
        and all(isinstance(v, str) and v.strip() for v in value)
    )


def _check_stats(stats: Any) -> tuple[str, str] | None:
    if stats is None:
        return None
    if not isinstance(stats, list) or len(stats) > MAX_STATS:
        return "bad_stats", f"stats must be an array of at most {MAX_STATS}"
    for s in stats:
        if (
            not isinstance(s, dict)
            or set(s) != {"label", "value"}
            or not all(isinstance(s[k], str) and s[k].strip() for k in ("label", "value"))
        ):
            return "bad_stats", "each stat is {label, value} strings"
    return None


def _check_section(i: int, section: Any) -> tuple[str, str] | None:
    if not isinstance(section, dict):
        return "bad_section", f"section {i + 1} must be an object"
    kind = section.get("kind")
    title = section.get("title")
    if title is not None and (not isinstance(title, str) or len(title) > MAX_TITLE):
        return "bad_title", f"section {i + 1}: title is a string of at most {MAX_TITLE}"
    if kind == "table":
        columns = section.get("columns")
        rows = section.get("rows")
        if not isinstance(columns, list) or not _string_list(columns, 12):
            return "bad_table", f"table section {i + 1}: columns must be 1-12 strings"
        if not isinstance(rows, list) or not rows or len(rows) > MAX_TABLE_ROWS:
            return "bad_table", f"table section {i + 1}: rows must be 1-{MAX_TABLE_ROWS} arrays"
        for r, row in enumerate(rows):
            if not (
                isinstance(row, list)
                and len(row) == len(columns)
                and all(isinstance(c, str) and len(c) <= CELL_MAX_CHARS for c in row)
            ):
                return (
                    "bad_table",
                    f"table section {i + 1}: row {r + 1} must be {len(columns)} strings",
                )
        note = section.get("note")
        if note is not None and not isinstance(note, str):
            return "bad_table", f"table section {i + 1}: note must be a string"
        if set(section) - {"kind", "title", "columns", "rows", "note"}:
            return "bad_table", f"table section {i + 1}: unknown fields"
        return None
    if kind == "prose":
        paragraphs = section.get("paragraphs")
        if not isinstance(paragraphs, list) or not _string_list(paragraphs, MAX_PROSE_PARAGRAPHS):
            return (
                "bad_prose",
                f"prose section {i + 1}: paragraphs must be 1-{MAX_PROSE_PARAGRAPHS} strings",
            )
        for p, para in enumerate(paragraphs):
            if (
                len(para) > PROSE_MAX_CHARS
                or len(_SENTENCE_END.findall(para)) > MAX_PROSE_SENTENCES
            ):
                return (
                    "bad_prose",
                    f"prose section {i + 1}: paragraph {p + 1} exceeds two sentences — "
                    "the detail belongs in a table",
                )
        if set(section) - {"kind", "title", "paragraphs"}:
            return "bad_prose", f"prose section {i + 1}: unknown fields"
        return None
    if kind == "callout":
        text = section.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > PROSE_MAX_CHARS:
            return "bad_callout", f"callout section {i + 1}: text must be a short string"
        tone = section.get("tone")
        if tone is not None and tone not in TONES:
            return "bad_callout", f"callout section {i + 1}: tone is one of {TONES}"
        if set(section) - {"kind", "title", "text", "tone"}:
            return "bad_callout", f"callout section {i + 1}: unknown fields"
        return None
    if kind == "steps":
        if not _string_list(section.get("items"), MAX_STEPS):
            return "bad_steps", f"steps section {i + 1}: items must be 1-{MAX_STEPS} strings"
        if set(section) - {"kind", "title", "items"}:
            return "bad_steps", f"steps section {i + 1}: unknown fields"
        return None
    return "bad_kind", f"section {i + 1}: kind must be table, prose, callout or steps"


def rewrite(
    payload: dict[str, Any], ids: dict[str, Row]
) -> tuple[dict[str, Any], dict[str, Row]] | None:
    """Check every ``[R3]`` citation in the payload against the rows the model was sent and
    rewrite them to row-key markers (the same conversion ``checked`` gives Markdown). A
    citation of a row that was not sent is repaired, as in Markdown (token review item 6,
    2026-10-20: was a discarded reply): an item of a list (a table row, a list entry) whose
    citations are ALL unknown is dropped, and an unknown id elsewhere is removed. None when
    every citation was unknown (the local answer then stands)."""
    rows: dict[str, Row] = {}
    dropped: list[str] = []

    def cites(value: Any) -> list[str]:
        if isinstance(value, str):
            return _CITE.findall(value)
        if isinstance(value, list):
            return [rid for v in value for rid in cites(v)]
        if isinstance(value, dict):
            return [rid for v in value.values() for rid in cites(v)]
        return []

    def swap(value: Any) -> Any:
        if isinstance(value, str):
            dropped.extend(rid for rid in _CITE.findall(value) if rid not in ids)
            return _CITE.sub(lambda m: mark(ids[m.group(1)]) if m.group(1) in ids else "", value)
        if isinstance(value, list):
            kept = []
            for v in value:
                found = cites(v)
                if found and all(rid not in ids for rid in found):
                    dropped.extend(found)  # the item rests only on rows that were not sent
                    continue
                kept.append(swap(v))
            return kept
        if isinstance(value, dict):
            return {k: swap(v) for k, v in value.items()}
        return value

    def mark(row: Row) -> str:
        rows[row.key] = row
        return f"[[{row.key}]]"

    work = dict(payload)
    if not isinstance(work.get("stats"), list):
        work["stats"] = []  # the client schema requires the key
    out = swap(work)
    if dropped:
        log.warning(
            "ask.citation_dropped",
            extra={"where": "render", "count": len(dropped), "ids": ",".join(dropped[:20])},
        )
    if dropped and not rows:
        return None  # every citation pointed at a row that was not sent
    return out, rows


def to_draft(headline_payload: dict[str, Any], rows: dict[str, Row]) -> Draft:
    """The validated payload as a draft: ``structured`` for the client, plus the same answer
    as Markdown paragraphs (the stream, the stored plain text and the citation numbering all
    come from this representation — one answer, two renderings)."""
    d = Draft()
    d.rows = dict(rows)
    d.structured = headline_payload
    # plain headline, no bold wrapper: a [[key]] citation inside **…** would travel inside the
    # bold part and never become a chip (bold wins in the _MARK alternation)
    d.para(str(headline_payload["headline"]).strip())
    stats = headline_payload.get("stats") or []
    if stats:
        d.para(" · ".join(f"**{s['value']}** {s['label']}" for s in stats))
    for section in headline_payload["sections"]:
        _section_to_markdown(d, section)
    return d


def _section_to_markdown(d: Draft, section: dict[str, Any]) -> None:
    title = section.get("title")
    if title:
        d.para(f"**{title}**")
    kind = section["kind"]
    if kind == "table":
        columns = [str(c) for c in section["columns"]]
        lines = [
            "| " + " | ".join(columns) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
        ]
        for row in section["rows"]:
            lines.append("| " + " | ".join(str(c) for c in row) + " |")
        d.para("\n".join(lines))
        if section.get("note"):
            d.para(str(section["note"]))
    elif kind == "prose":
        for para in section["paragraphs"]:
            d.para(str(para))
    elif kind == "callout":
        d.para("> " + str(section["text"]))
    else:  # steps
        d.para("\n".join(f"{i}. {item}" for i, item in enumerate(section["items"], 1)))
