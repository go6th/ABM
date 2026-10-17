"""Parsed units to passages: the chunk is the unit of retrieval, embedding and citation. Every
unit's route is its shape from ``documents.shapes`` (rows, timestamped speaker turns, or prose —
never the file type it came from); ``ShapeLog`` logs the decision once per member.

* Documents (PDF pages, Word sections, slides, text sections): structure aware, 400 to 800
  tokens, about 12% overlap; the heading path (document name, the member inside a zip, then the
  section, page or slide) is kept with each passage. Small consecutive units under one heading
  merge until the minimum. A section shorter than ``MERGE_BELOW`` of the minimum (a title line,
  a page footer, a one-line heading) merges forward with the sections after it, each merged
  section keeping its heading as its first line, so a transcript with a running header does not
  become hundreds of one-line passages (McKesson workspace, 2026-10-16); a short tail merges
  back into the passage before it. A section that reaches that floor is never split across a
  heading.
* Call transcripts (text whose lines are mostly timestamped speaker turns): windows of whole
  speaker turns of 60 to 90 seconds, speaker labels kept.
* CRM exports and spreadsheets (rows with a header): one row per passage, ``column: value``,
  with every cell typed (ISO or unambiguous day-/month-first date, number, boolean or text,
  under the normalised column name) in ``Passage.metadata``, which is what the table store and
  metadata filters read; a mixed column keeps type ``text`` and the conflict is on record
  (``type_conflicts``), never hidden. The passage
  text is a projection: the row's short, filterable columns first; a payload column (a long
  description: median cell over ``table_payload_min_chars``, or a name in
  ``table_payload_columns``) is never in the row's first part, so the row's embedding is not
  95% boilerplate, and goes whole into the parts after it (``part`` and ``parts`` in the
  location), each payload field unsplit; joining the parts with ``; `` gives the row back.
  Filterable columns are never split across parts. A table that would pass the passage cap
  raises ``TableIngestTruncated`` instead of dropping rows silently (documents keep the
  ``truncated`` flag).

Every passage carries its location (page, slide, section and character span, sheet and row,
or the transcript's time range), which is what a fact cites. The text is the file's own text:
every non-empty line of a member is in some passage (overlap repeats, nothing is dropped or
rewritten beyond redaction).

GUARANTEE(test_every_line_of_a_file_is_in_a_passage_and_short_sections_merge)

GUARANTEE(test_chunking_follows_the_three_shapes)

GUARANTEE(test_a_row_embeds_its_short_columns_and_keeps_the_description_as_payload)

GUARANTEE(test_a_table_past_the_passage_cap_raises_instead_of_dropping_rows)

GUARANTEE(test_cells_type_booleans_and_dates_and_conflicts_are_recorded)
"""

from __future__ import annotations

import fnmatch
import hashlib
import math
import re
from collections.abc import Generator, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from statistics import median
from typing import Any

from account_intelligence.rag.tokenizer import Calibration, calibrate
from documents.config import ChunkingCfg
from documents.redact import Redactor
from documents.sandbox import ParsedUnit
from documents.shapes import Shape, ShapeLog, member_of
from documents.shapes import turns as shape_turns

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:T00:00:00(?:\.0+)?)?$")
_INT = re.compile(r"^-?(?:0|[1-9]\d*)$")
_FLOAT = re.compile(r"^-?(?:0|[1-9]\d*)\.\d+$")
_SPACES = re.compile(r"\s+")
# a slash/dot/dash date without the year first: day-first (EU, the workspace's convention) when
# the first number cannot be a month, month-first (US) when the second cannot; when both could
# be a month the value stays TEXT — never invent a date from an ambiguous cell (B3 determinism)
_DMY_MDY_DATE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_BOOL = {"true": True, "false": False}

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n{2,}")
MERGE_BELOW = 0.25  # of document_min_tokens: a shorter section merges with its neighbours
_LIST_SPLIT = re.compile(r"\s*[,;|]\s*")
LIST_ITEM_MAX_CHARS = 48  # a longer item is a sentence, not a list entry


def list_items(text: str) -> list[str] | None:
    """The items of a list cell (``Veeva, Salesforce``): at least two separated by a comma,
    semicolon or pipe, each non-empty and short; None when the cell is not such a list. Shared
    with ``documents.tables`` (detection and whole-item matching agree)."""
    items = [item.strip() for item in _LIST_SPLIT.split(text.strip())]
    if len(items) < 2 or not all(items) or any(len(i) > LIST_ITEM_MAX_CHARS for i in items):
        return None
    return items


@dataclass
class Passage:
    """One chunk ready to store (see the module docstring)."""

    seq: int
    kind: str  # section | transcript_window | crm_row | text
    location: dict[str, Any]
    heading_path: str | None
    text: str
    tokens: int
    sha256: str = field(init=False)
    # a crm_row's cells, typed, under the normalised column names (every column, payload too);
    # empty for every other kind and for a row's payload parts
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.sha256 = hashlib.sha256(self.text.encode("utf-8")).hexdigest()


class TableIngestTruncated(Exception):
    """A table has more rows than the passage cap allows: the ingest of that member fails
    rather than storing part of the table as if it were whole. ``kept`` rows were emitted;
    ``total`` is the rows seen when the cap was hit (the parser streams, so at least that)."""

    def __init__(self, table: str, kept: int, total: int) -> None:
        super().__init__(f"table {table!r} has more than {kept} rows (at least {total})")
        self.table, self.kept, self.total = table, kept, total


@dataclass
class Column:
    """One column of an ingested table: its normalised name, the header as written, the type
    tally of its non-empty cells and whether it is payload (not in the row's first part);
    ``lists`` tallies the text cells that hold a comma/semicolon/pipe list of short items, so
    the column can be marked multi-valued (a fifth or more of its text cells do)."""

    name: str
    raw: str
    payload: bool = False
    dates: int = 0
    numbers: int = 0
    texts: int = 0
    booleans: int = 0
    lists: int = 0

    @property
    def type(self) -> str:
        """``date``, ``number`` or ``boolean`` when every non-empty cell is one, else ``text``."""
        kinds = [bool(self.dates), bool(self.numbers), bool(self.texts), bool(self.booleans)]
        if sum(kinds) != 1:
            return "text"
        if self.dates:
            return "date"
        if self.numbers:
            return "number"
        return "boolean" if self.booleans else "text"

    @property
    def conflicting(self) -> bool:
        """Cells of more than one kind sat in this column (``type_conflicts`` in the table's
        record): a mixed column keeps type ``text`` and the conflict is on record, not hidden."""
        return sum(bool(n) for n in (self.dates, self.numbers, self.texts, self.booleans)) > 1

    @property
    def multi_valued(self) -> bool:
        """A text column whose cells are often a list (``Platforms named``): the predicates
        ``contains``/``icontains`` then match a whole item first, a substring second."""
        return bool(self.texts and self.lists * 5 >= self.texts)

    def tally(self, kind: str) -> None:
        """Count one non-empty cell of ``kind``."""
        if kind == "date":
            self.dates += 1
        elif kind == "number":
            self.numbers += 1
        elif kind == "boolean":
            self.booleans += 1
        elif kind == "text":
            self.texts += 1


@dataclass
class TableSchema:
    """What the chunker learned about one table: the columns in header order, the payload
    columns, the business key (the column duplicate rows collapse on) and the rows seen."""

    table: str
    member: str | None
    columns: list[Column]
    business_key: str | None = None
    rows: int = 0

    def record(self) -> dict[str, Any]:
        """The JSON record the store keeps (``document_tables.columns`` and friends)."""
        return {
            "columns": [
                {
                    "name": c.name,
                    "raw": c.raw,
                    "type": c.type,
                    "payload": c.payload,
                    "multi_valued": c.multi_valued,
                    "dates": c.dates,
                    "numbers": c.numbers,
                    "texts": c.texts,
                    "booleans": c.booleans,
                    "lists": c.lists,
                }
                for c in self.columns
            ],
            "payload_columns": [c.name for c in self.columns if c.payload],
            "type_conflicts": [c.name for c in self.columns if c.conflicting],
            "business_key": self.business_key,
            "row_count": self.rows,
        }


def normalise_column(raw: str, taken: set[str], index: int) -> str:
    """The normalised column name: case folded, spaces to underscores, unique in the table
    (``_2``, ``_3`` for a repeated header, ``column_<n>`` for an empty one)."""
    base = _SPACES.sub("_", str(raw).strip().casefold()) or f"column_{index + 1}"
    name, n = base, 1
    # LOOP(unique column name): cap=the table's columns; exit=a name no column has
    while name in taken:
        n += 1
        name = f"{base}_{n}"
    taken.add(name)
    return name


def typed_cell(value: Any) -> tuple[Any, str]:
    """A cell as the typed value the store keeps and its kind: ``(None, "null")`` for an
    empty cell, a boolean (a real bool or the exact string ``true``/``false``), an ISO date
    string (``YYYY-MM-DD``, a midnight timestamp included) as that date, a day- or month-first
    date whose month is unambiguous (``31/12/2024``, ``12/31/2024``; an ambiguous ``05/06/2024``
    stays text — never invent a date), an int or float (or a plain numeric string without a
    leading zero) as a number, anything else as text."""
    if value is None:
        return None, "null"
    if isinstance(value, bool):
        return value, "boolean"
    if isinstance(value, int | float):
        return value, "number"
    text = str(value).strip()
    if not text:
        return None, "null"
    m = _ISO_DATE.match(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), "date"
        except ValueError:
            return text, "text"
    m = _DMY_MDY_DATE.match(text)
    if m:
        first, second, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if first <= 12 and second <= 12:
            return text, "text"  # ambiguous: no date from a guess
        day, month = (first, second) if first > 12 else (second, first)
        try:
            return date(year, month, day).isoformat(), "date"
        except ValueError:
            return text, "text"
    if text.casefold() in _BOOL:
        return _BOOL[text.casefold()], "boolean"
    if _INT.match(text):
        return int(text), "number"
    if _FLOAT.match(text):
        return float(text), "number"
    return text, "text"


def cell_text(value: Any) -> str:
    """The typed cell as the passage text shows it (a boolean as the lowercase word the file
    would use)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def tokens(text: str, cfg: ChunkingCfg) -> int:
    """Estimated tokens of ``text`` (no tokenizer is bundled)."""
    return math.ceil(len(text) / cfg.chars_per_token)


def _split(text: str, max_chars: int, min_chars: int, overlap: int) -> list[tuple[int, int]]:
    """Character spans of at most ``max_chars``, cut at sentence or paragraph ends where one
    exists past ``min_chars``, with ``overlap`` characters repeated between spans."""
    if len(text) <= max_chars:
        return [(0, len(text))]
    spans: list[tuple[int, int]] = []
    start = 0
    # LOOP(passage split): cap=len(text) / (max_chars - overlap) + 1 spans; exit=end of text
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            window = text[start + min_chars : end]
            cuts = [m.end() for m in _SENTENCE_END.finditer(window)]
            if cuts:
                end = start + min_chars + cuts[-1]
        spans.append((start, end))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return spans


class Chunker:
    """Units in, passages out, in order (see the module docstring)."""

    def __init__(
        self,
        cfg: ChunkingCfg,
        name: str,
        redactor: Redactor,
        max_passages: int,
        first_seq: int = 0,
        shape_log: ShapeLog | None = None,
    ):
        self.cfg = cfg
        self.name = name
        self.redactor = redactor
        self.max_passages = max_passages
        self.first_seq = first_seq  # a part's passages are numbered from its own range
        self.seq = first_seq
        self.headers: dict[str, list[str]] = {}
        self.tables: dict[str, TableSchema] = {}  # what was learned about each table
        self.truncated = False
        self.shape_log = shape_log  # one logged ``documents.shape`` decision per member (B2)
        # the token budget is calibrated once per file from its own first text (B4; see _tokens)
        self._cal: Calibration | None = None
        # carried between calls: the service hands units one at a time, and short sections
        # merge across those calls; ``finish`` (or ``final=True``) closes the file
        self._pending: list[ParsedUnit] = []
        self._held: Passage | None = None  # the last section passage, open for a short tail

    def _note(self, unit: ParsedUnit, shape: Shape) -> None:
        """Log this member's shape decision once (routing itself stays per unit)."""
        if self.shape_log is not None:
            self.shape_log.note(unit, shape)

    def _calibration(self, sample: str) -> Calibration:
        """This file's calibration, primed from ``sample`` when it is the first text budgeted."""
        if self._cal is None:
            self._cal = calibrate(sample, float(self.cfg.chars_per_token))
        return self._cal

    def _tokens(self, text: str) -> int:
        """Budget tokens of ``text``: a real tokenizer when the runtime has one, else the
        configured chars per token calibrated once from this file's own first text — a Japanese
        transcript or a German contract gets the budget that fits it, not the English estimate
        (B4). The calibration is a pure function of the sample: the same file always parts the
        same way."""
        return self._calibration(text).tokens(text)

    def _next(
        self, kind: str, location: dict[str, Any], heading: str | None, text: str
    ) -> Passage | None:
        if self.seq - self.first_seq >= self.max_passages:
            self.truncated = True
            return None
        self.seq += 1
        member = member_of(location)
        path = self.name
        if member and member != heading:
            path = f"{path} > {member}"
        if heading:
            path = f"{path} > {heading}"
        p = Passage(self.seq, kind, location, path[:500], text, self._tokens(text))
        if kind != "crm_row":  # a row's metadata is its typed cells; the rest carry B4/B5's
            p.metadata = passage_meta(kind, location, p.heading_path)
        return p

    @property
    def _floor(self) -> int:
        return max(1, int(self.cfg.document_min_tokens * MERGE_BELOW))

    def passages(self, units: Iterable[ParsedUnit], *, final: bool = True) -> Iterator[Passage]:
        """The passages these units complete, in order. With ``final`` (the default) the file
        ends here and everything pending comes out; the service passes ``final=False`` unit by
        unit and calls ``finish`` once, so short sections merge across its calls."""
        for unit in units:
            if unit.fatal:
                continue
            if unit.rows is not None:
                self._note(unit, "tabular")
                yield from self.finish()
                yield from self._rows(unit)
                continue
            if not unit.text.strip():
                continue
            found = shape_turns(unit.text)
            if found is not None:
                self._note(unit, "transcript")
                yield from self.finish()
                yield from self._transcript(unit, found)
                continue
            self._note(unit, "prose")
            pending = self._pending
            if pending and member_of(unit.location) != member_of(pending[-1].location):
                yield from self.finish()  # another file in the zip: nothing merges across
                pending = self._pending
            if (
                pending
                and self._heading(unit) not in (None, self._heading(pending[-1]))
                and sum(self._tokens(u.text) for u in pending) >= self._floor
            ):
                # a new heading after a section of real size: the section stands on its own;
                # a shorter one (a title line, a footer) merges forward instead
                self._held = yield from self._settle(self._held, self._flush(pending))
                self._pending = pending = []
            pending.append(unit)
            size = sum(self._tokens(u.text) for u in pending)
            if size >= self.cfg.document_min_tokens:
                self._held = yield from self._settle(self._held, self._flush(pending))
                self._pending = []
        if final:
            yield from self.finish()

    def finish(self) -> Iterator[Passage]:
        """Close the open section: what is pending joins or follows the held passage, and the
        held passage comes out."""
        self._held = yield from self._settle(self._held, self._flush(self._pending))
        self._pending = []
        if self._held is not None:
            yield self._held
            self._held = None

    def _settle(
        self, held: Passage | None, new: Iterable[Passage]
    ) -> Generator[Passage, None, Passage | None]:
        """Yield section passages one behind, so a short tail can join the passage before it."""
        for p in new:
            if held is None:
                held = p
            elif self._joins(held, p):
                held = self._joined(held, p)
            else:
                yield held
                held = p
        return held

    def _joins(self, held: Passage, tail: Passage) -> bool:
        return (
            tail.kind == "section"
            and held.kind == "section"
            and tail.tokens < self._floor
            and held.tokens + tail.tokens <= self.cfg.document_max_tokens
            and member_of(held.location) == member_of(tail.location)
        )

    def _joined(self, held: Passage, tail: Passage) -> Passage:
        self.seq -= 1  # the tail's number is given back: the passage keeps the held one's
        heading = tail.heading_path.rsplit(" > ", 1)[-1] if tail.heading_path else ""
        before = held.location.get("to", held.location).get("section")
        lead = f"{heading}\n" if heading and heading != before else ""
        text = f"{held.text}\n\n{lead}{tail.text}"
        start = held.location.get("from", held.location)
        end = tail.location.get("to", tail.location)
        at = {k: v for k, v in start.items() if k != "chars"}
        location = at if start == end else {"from": at, "to": {k: v for k, v in end.items()}}
        joined = Passage(held.seq, "section", location, held.heading_path, text, self._tokens(text))
        joined.metadata = passage_meta("section", location, held.heading_path)
        return joined

    @staticmethod
    def _heading(unit: ParsedUnit) -> str | None:
        loc = unit.location
        if unit.heading:
            return str(unit.heading)
        if "section" in loc and loc["section"] != "(no heading)":
            return str(loc["section"])
        return None

    def _flush(self, units: list[ParsedUnit]) -> Iterator[Passage]:
        if not units:
            return
        heading = next((h for h in (self._heading(u) for u in units) if h), None)
        if heading is None:
            first = units[0].location
            if "page" in first:
                heading = f"Page {first['page']}"
            elif "slide" in first:
                heading = f"Slide {first['slide']}"
        # merged sections keep their own heading as a first line (the file's text, in order)
        blocks: list[str] = []
        last: str | None = self._heading(units[0])
        for i, u in enumerate(units):
            own = self._heading(u)
            lead = f"{own}\n" if i and own and own != last else ""
            blocks.append(lead + self.redactor.text(u.text.strip()))
            last = own or last
        text = "\n\n".join(blocks)
        base: dict[str, Any] = (
            units[0].location
            if len(units) == 1
            else {
                "from": units[0].location,
                "to": units[-1].location,
            }
        )
        ratio = self._calibration(text).chars_per_token  # the split width must fit the file too
        max_chars = int(self.cfg.document_max_tokens * ratio)
        min_chars = int(self.cfg.document_min_tokens * ratio)
        overlap = int(max_chars * self.cfg.overlap_fraction)
        span = base.get("lines") if len(units) == 1 else None
        for start, end in _split(text, max_chars, min_chars, overlap):
            body = text[start:end].strip()
            if not body:
                continue
            at: dict[str, Any] = {**base, "chars": [start, end]}
            if span:  # the window's own lines, not the whole section's
                line_one = int(span[0])
                at["lines"] = [
                    line_one + text.count("\n", 0, start),
                    line_one + text.count("\n", 0, end),
                ]
            p = self._next("section", at, heading, body)
            if p is None:
                return
            yield p

    def _rows(self, unit: ParsedUnit) -> Iterator[Passage]:
        table = str(unit.location.get("table") or unit.table or "table")
        if unit.header:
            self.headers[table] = [str(h) for h in unit.header]
        header = self.headers.get(table, [])
        rows = unit.rows or []
        schema = self.tables.get(table)
        if schema is None:
            schema = self._schema(table, unit, header, rows)
            self.tables[table] = schema
        first_row = [str(c) for c in rows[0][1]] if rows else []
        ratio = self._calibration("; ".join([*header, *first_row])).chars_per_token
        max_chars = int(self.cfg.document_max_tokens * ratio)
        for row_no, cells in rows:
            short: list[str] = []
            payload: list[str] = []
            metadata: dict[str, Any] = {}
            for i, cell in enumerate(cells):
                column = schema.columns[i] if i < len(schema.columns) else None
                if column is None:  # a cell past the header: named by its position
                    column = Column(normalise_column("", {c.name for c in schema.columns}, i), "")
                    column.raw = f"column {i + 1}"
                    schema.columns.append(column)
                raw = None if cell is None else self.redactor.cell(column.raw, str(cell))
                value, kind = typed_cell(raw)
                metadata[column.name] = value
                if value is None:
                    continue
                column.tally(kind)
                if kind == "text" and list_items(cell_text(value)):
                    column.lists += 1
                (payload if column.payload else short).append(f"{column.raw}: {cell_text(value)}")
            if not short and not payload:
                continue
            schema.rows += 1
            if schema.rows > self.cfg.max_table_rows:  # never a partial table (B3)
                raise TableIngestTruncated(table, self.cfg.max_table_rows, schema.rows)
            row_at: dict[str, Any] = {"table": table, "row": row_no}
            if unit.location.get("member"):  # a sheet inside a zip keeps the file it came from
                row_at["member"] = unit.location["member"]
            pieces = _row_parts(short, payload, max_chars)
            for i, piece in enumerate(pieces, 1):
                at = {**row_at, "part": i, "parts": len(pieces)} if len(pieces) > 1 else row_at
                p = self._next("crm_row", at, table, piece)
                if p is None:
                    kept = schema.rows - 1
                    raise TableIngestTruncated(table, kept, kept + 1)
                if i == 1:
                    p.metadata = metadata
                yield p

    def _schema(
        self, table: str, unit: ParsedUnit, header: list[str], rows: list[tuple[int, list[Any]]]
    ) -> TableSchema:
        """The table's columns from its header, with the payload columns decided on the first
        unit's rows (a column whose median non-empty cell is longer than
        ``table_payload_min_chars``, or whose name is in ``table_payload_columns``) and the
        business key (the per-table rule, else the first of ``table_business_keys`` present)."""
        width = max([len(header), *(len(cells) for _, cells in rows)], default=0)
        taken: set[str] = set()
        columns = [
            Column(normalise_column(header[i] if i < len(header) else "", taken, i), header[i])
            if i < len(header)
            else Column(normalise_column("", taken, i), f"column {i + 1}")
            for i in range(width)
        ]
        explicit = {name.casefold() for name in self.cfg.table_payload_columns}
        for i, column in enumerate(columns):
            lengths = [
                len(str(cells[i]).strip())
                for _, cells in rows
                if i < len(cells) and cells[i] is not None and str(cells[i]).strip()
            ]
            column.payload = column.name in explicit or bool(
                lengths and median(lengths) > self.cfg.table_payload_min_chars
            )
        names = {c.name for c in columns}
        key = next(
            (
                column
                for pattern, column in self.cfg.table_business_key.items()
                if fnmatch.fnmatchcase(table, pattern) and column in names
            ),
            None,
        ) or next((k for k in self.cfg.table_business_keys if k in names), None)
        member = unit.location.get("member")
        return TableSchema(table, str(member) if member else None, columns, key)

    def _transcript(
        self, unit: ParsedUnit, turns: list[tuple[float, str, str, int]]
    ) -> Iterator[Passage]:
        window: list[tuple[float, str, str, int]] = []
        for turn in turns:
            if window:
                span = turn[0] - window[0][0]
                if span >= self.cfg.transcript_max_s or (span >= self.cfg.transcript_min_s):
                    p = self._window(unit, window, turn[0])
                    if p is None:
                        return
                    yield p
                    window = []
            window.append(turn)
        if window:
            p = self._window(unit, window, None)
            if p is not None:
                yield p

    def _window(
        self, unit: ParsedUnit, window: list[tuple[float, str, str, int]], next_start: float | None
    ) -> Passage | None:
        lines = [
            f"{self.redactor.speaker(s)}: {self.redactor.text(t.strip())}" for _, s, t, _ in window
        ]
        end = next_start if next_start is not None else window[-1][0]
        location = {
            **{k: v for k, v in unit.location.items() if k in ("section", "page", "member")},
            "start_s": window[0][0],
            "end_s": end,
            "lines": [window[0][3], window[-1][3]],
        }
        p = self._next("transcript_window", location, "Call transcript", "\n".join(lines))
        if p is not None:  # the window's own speakers and time range (B5), redacted like the text
            p.metadata |= {
                "speakers": sorted({self.redactor.speaker(s) for _, s, _, _ in window}),
                "start_s": window[0][0],
                "end_s": end,
            }
        return p


_PROSE_LOC_KEYS = ("member", "page", "slide", "section")


def passage_meta(kind: str, location: dict[str, Any], heading_path: str | None) -> dict[str, Any]:
    """The filterable record of a prose or transcript passage (brief B4/B5): its shape, the
    member of an archive it is in, its heading path, and the page/slide/section it spans. A row
    passage carries its typed cells instead, and the store stamps ``ingested_at`` on every one."""
    meta: dict[str, Any] = {"kind": kind}
    start = location.get("from", location)
    for key in _PROSE_LOC_KEYS:
        value = location.get(key, start.get(key))
        if value is not None:
            meta[key] = value
    if heading_path:
        meta["heading_path"] = heading_path
    return meta


def _row_parts(short: list[str], payload: list[str], max_chars: int) -> list[str]:
    """The row's ``column: value`` fields joined with ``; `` into pieces: the short (filterable)
    fields are always one piece, never split; the payload fields follow in pieces of at most
    ``max_chars`` where the fields allow (a field longer than that stays whole, never cut), so
    ``"; ".join(parts)`` is exactly the row. A row without payload is one piece."""
    pieces: list[str] = []
    if short:
        pieces.append("; ".join(short))
    current: list[str] = []
    size = 0
    for f in payload:
        if current and size + 2 + len(f) > max_chars:
            pieces.append("; ".join(current))
            current, size = [], 0
        current.append(f)
        size += len(f) + (2 if size else 0)
    if current:
        pieces.append("; ".join(current))
    return pieces


def cite(location: dict[str, Any]) -> str:
    """A person-readable location: page 3, slide 2, row 14 of Contacts, 00:01:00 to 00:02:30;
    inside a zip, the member file first ("plans/renewal.docx, page 3")."""
    loc = location.get("from", location)
    member = loc.get("member") or location.get("member")
    if member:
        inner = {k: v for k, v in loc.items() if k != "member"}
        rest = cite(
            {
                **{k: v for k, v in location.items() if k not in ("member", "from")},
                **({"from": inner} if "from" in location else inner),
            }
        )
        return str(member) if rest == "the document" else f"{member}, {rest}"
    if "start_s" in location:
        return f"{_clock(location['start_s'])} to {_clock(location['end_s'])}"
    if "row" in loc:
        where = f"row {loc['row']} of {loc.get('table') or loc.get('sheet') or 'the sheet'}"
        if loc.get("parts", 1) > 1:
            where += f" (part {loc['part']} of {loc['parts']})"
        return where
    if "page" in loc:
        last = location.get("to", {}).get("page")
        return f"page {loc['page']}" + (f" to {last}" if last and last != loc["page"] else "")
    if "slide" in loc:
        return f"slide {loc['slide']}"
    if "section" in loc and loc["section"] != "(no heading)":
        return f"section {loc['section']}"
    if "lines" in loc:
        return f"lines {loc['lines'][0]} to {loc['lines'][1]}"
    return "the document"


def _clock(value: float) -> str:
    s = int(value)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
