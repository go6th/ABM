"""Parsed units to passages: the chunk is the unit of retrieval, embedding and citation.

* Documents (PDF pages, Word sections, slides, text sections): structure aware, 400 to 800
  tokens, about 12% overlap, never across a heading; the heading path (document name, then the
  section, page or slide) is kept with each passage. Small consecutive units under one heading
  merge until the minimum.
* Call transcripts (text whose lines are mostly timestamped speaker turns): windows of whole
  speaker turns of 60 to 90 seconds, speaker labels kept.
* CRM exports and spreadsheets (rows with a header): one row per passage, ``column: value``.

Every passage carries its location (page, slide, section and character span, sheet and row,
or the transcript's time range), which is what a fact cites.

GUARANTEE(test_chunking_follows_the_three_shapes)
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from documents.config import ChunkingCfg
from documents.redact import Redactor
from documents.sandbox import ParsedUnit

_TURN = re.compile(
    r"^\s*\[?(?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)\]?\s*[-–]?\s*"
    r"(?P<speaker>[A-Z][\w .'&-]{0,40}?)\s*:\s*(?P<text>.*)$"
)
_TURN_AFTER = re.compile(
    r"^\s*(?P<speaker>[A-Z][\w .'&-]{0,40}?)\s*[\[(](?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})[\])]\s*:?\s*"
    r"(?P<text>.*)$"
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n{2,}")


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

    def __post_init__(self) -> None:
        self.sha256 = hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def tokens(text: str, cfg: ChunkingCfg) -> int:
    """Estimated tokens of ``text`` (no tokenizer is bundled)."""
    return math.ceil(len(text) / cfg.chars_per_token)


def seconds(ts: str) -> float:
    """``h:mm:ss`` or ``mm:ss`` (with optional fraction) to seconds."""
    parts = [float(p.replace(",", ".")) for p in ts.split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def _turns(text: str) -> list[tuple[float, str, str, int]] | None:
    """Timestamped speaker turns ``(seconds, speaker, text, line)`` when the text is a transcript
    (at least 60% of its non-empty lines, and at least 4 turns), else None."""
    lines = [ln for ln in text.splitlines()]
    turns: list[tuple[float, str, str, int]] = []
    nonempty = 0
    for no, line in enumerate(lines, 1):
        if not line.strip() or line.strip().upper() == "WEBVTT":
            continue
        nonempty += 1
        m = _TURN.match(line) or _TURN_AFTER.match(line)
        if m:
            turns.append((seconds(m.group("ts")), m.group("speaker").strip(), m.group("text"), no))
        elif turns:
            # a continuation line belongs to the previous turn
            t = turns[-1]
            turns[-1] = (t[0], t[1], f"{t[2]} {line.strip()}", t[3])
            nonempty -= 1
    if len(turns) >= 4 and len(turns) >= 0.6 * max(nonempty, 1):
        return turns
    return None


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

    def __init__(self, cfg: ChunkingCfg, name: str, redactor: Redactor, max_passages: int):
        self.cfg = cfg
        self.name = name
        self.redactor = redactor
        self.max_passages = max_passages
        self.seq = 0
        self.headers: dict[str, list[str]] = {}
        self.truncated = False

    def _next(
        self, kind: str, location: dict[str, Any], heading: str | None, text: str
    ) -> Passage | None:
        if self.seq >= self.max_passages:
            self.truncated = True
            return None
        self.seq += 1
        path = f"{self.name} > {heading}" if heading else self.name
        return Passage(self.seq, kind, location, path[:500], text, tokens(text, self.cfg))

    def passages(self, units: Iterable[ParsedUnit]) -> Iterator[Passage]:
        """Every passage of the document."""
        pending: list[ParsedUnit] = []
        for unit in units:
            if unit.fatal:
                continue
            if unit.rows is not None:
                yield from self._flush(pending)
                pending = []
                yield from self._rows(unit)
                continue
            if not unit.text.strip():
                continue
            turns = _turns(unit.text)
            if turns is not None:
                yield from self._flush(pending)
                pending = []
                yield from self._transcript(unit, turns)
                continue
            if pending and self._heading(unit) not in (None, self._heading(pending[0])):
                yield from self._flush(pending)
                pending = []
            pending.append(unit)
            size = sum(tokens(u.text, self.cfg) for u in pending)
            if size >= self.cfg.document_min_tokens:
                yield from self._flush(pending)
                pending = []
        yield from self._flush(pending)

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
        heading = self._heading(units[0])
        if heading is None:
            first = units[0].location
            if "page" in first:
                heading = f"Page {first['page']}"
            elif "slide" in first:
                heading = f"Slide {first['slide']}"
        text = "\n\n".join(self.redactor.text(u.text.strip()) for u in units)
        base = (
            units[0].location
            if len(units) == 1
            else {
                "from": units[0].location,
                "to": units[-1].location,
            }
        )
        max_chars = int(self.cfg.document_max_tokens * self.cfg.chars_per_token)
        min_chars = int(self.cfg.document_min_tokens * self.cfg.chars_per_token)
        overlap = int(max_chars * self.cfg.overlap_fraction)
        for start, end in _split(text, max_chars, min_chars, overlap):
            body = text[start:end].strip()
            if not body:
                continue
            p = self._next("section", {**base, "chars": [start, end]}, heading, body)
            if p is None:
                return
            yield p

    def _rows(self, unit: ParsedUnit) -> Iterator[Passage]:
        table = str(unit.location.get("table") or unit.table or "table")
        if unit.header:
            self.headers[table] = [str(h) for h in unit.header]
        header = self.headers.get(table, [])
        for row_no, cells in unit.rows or []:
            parts = []
            for i, cell in enumerate(cells):
                if cell is None or str(cell).strip() == "":
                    continue
                column = header[i] if i < len(header) else f"column {i + 1}"
                parts.append(f"{column}: {self.redactor.cell(column, str(cell))}")
            if not parts:
                continue
            p = self._next("crm_row", {"table": table, "row": row_no}, table, "; ".join(parts))
            if p is None:
                return
            yield p

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
            **{k: v for k, v in unit.location.items() if k in ("section", "page")},
            "start_s": window[0][0],
            "end_s": end,
            "lines": [window[0][3], window[-1][3]],
        }
        return self._next("transcript_window", location, "Call transcript", "\n".join(lines))


def cite(location: dict[str, Any]) -> str:
    """A person-readable location: page 3, slide 2, row 14 of Contacts, 00:01:00 to 00:02:30."""
    loc = location.get("from", location)
    if "start_s" in location:
        return f"{_clock(location['start_s'])} to {_clock(location['end_s'])}"
    if "row" in loc:
        return f"row {loc['row']} of {loc.get('table') or loc.get('sheet') or 'the sheet'}"
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
