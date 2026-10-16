"""Parsed units to passages: the chunk is the unit of retrieval, embedding and citation.

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
* CRM exports and spreadsheets (rows with a header): one row per passage, ``column: value``. A
  row longer than a passage (a JSON record with a nested list) is split at its ``; `` field
  boundaries into exact parts (``part`` and ``parts`` in the location); joining the parts with
  ``; `` gives the row back.

Every passage carries its location (page, slide, section and character span, sheet and row,
or the transcript's time range), which is what a fact cites. The text is the file's own text:
every non-empty line of a member is in some passage (overlap repeats, nothing is dropped or
rewritten beyond redaction).

GUARANTEE(test_every_line_of_a_file_is_in_a_passage_and_short_sections_merge)

GUARANTEE(test_chunking_follows_the_three_shapes)
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Generator, Iterable, Iterator
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
MERGE_BELOW = 0.25  # of document_min_tokens: a shorter section merges with its neighbours


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

    def __init__(
        self,
        cfg: ChunkingCfg,
        name: str,
        redactor: Redactor,
        max_passages: int,
        first_seq: int = 0,
    ):
        self.cfg = cfg
        self.name = name
        self.redactor = redactor
        self.max_passages = max_passages
        self.first_seq = first_seq  # a part's passages are numbered from its own range
        self.seq = first_seq
        self.headers: dict[str, list[str]] = {}
        self.truncated = False
        # carried between calls: the service hands units one at a time, and short sections
        # merge across those calls; ``finish`` (or ``final=True``) closes the file
        self._pending: list[ParsedUnit] = []
        self._held: Passage | None = None  # the last section passage, open for a short tail

    def _next(
        self, kind: str, location: dict[str, Any], heading: str | None, text: str
    ) -> Passage | None:
        if self.seq - self.first_seq >= self.max_passages:
            self.truncated = True
            return None
        self.seq += 1
        member = _member(location)
        path = self.name
        if member and member != heading:
            path = f"{path} > {member}"
        if heading:
            path = f"{path} > {heading}"
        return Passage(self.seq, kind, location, path[:500], text, tokens(text, self.cfg))

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
                yield from self.finish()
                yield from self._rows(unit)
                continue
            if not unit.text.strip():
                continue
            turns = _turns(unit.text)
            if turns is not None:
                yield from self.finish()
                yield from self._transcript(unit, turns)
                continue
            pending = self._pending
            if pending and _member(unit.location) != _member(pending[-1].location):
                yield from self.finish()  # another file in the zip: nothing merges across
                pending = self._pending
            if (
                pending
                and self._heading(unit) not in (None, self._heading(pending[-1]))
                and sum(tokens(u.text, self.cfg) for u in pending) >= self._floor
            ):
                # a new heading after a section of real size: the section stands on its own;
                # a shorter one (a title line, a footer) merges forward instead
                self._held = yield from self._settle(self._held, self._flush(pending))
                self._pending = pending = []
            pending.append(unit)
            size = sum(tokens(u.text, self.cfg) for u in pending)
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
            and _member(held.location) == _member(tail.location)
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
        return Passage(
            held.seq, "section", location, held.heading_path, text, tokens(text, self.cfg)
        )

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
        max_chars = int(self.cfg.document_max_tokens * self.cfg.chars_per_token)
        min_chars = int(self.cfg.document_min_tokens * self.cfg.chars_per_token)
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
        for row_no, cells in unit.rows or []:
            parts = []
            for i, cell in enumerate(cells):
                if cell is None or str(cell).strip() == "":
                    continue
                column = header[i] if i < len(header) else f"column {i + 1}"
                parts.append(f"{column}: {self.redactor.cell(column, str(cell))}")
            if not parts:
                continue
            row_at: dict[str, Any] = {"table": table, "row": row_no}
            if unit.location.get("member"):  # a sheet inside a zip keeps the file it came from
                row_at["member"] = unit.location["member"]
            max_chars = int(self.cfg.document_max_tokens * self.cfg.chars_per_token)
            pieces = _row_parts(parts, max_chars)
            for i, piece in enumerate(pieces, 1):
                at = {**row_at, "part": i, "parts": len(pieces)} if len(pieces) > 1 else row_at
                p = self._next("crm_row", at, table, piece)
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
            **{k: v for k, v in unit.location.items() if k in ("section", "page", "member")},
            "start_s": window[0][0],
            "end_s": end,
            "lines": [window[0][3], window[-1][3]],
        }
        return self._next("transcript_window", location, "Call transcript", "\n".join(lines))


def _member(location: dict[str, Any]) -> str | None:
    start = location.get("from", location)
    value = start.get("member") or location.get("member")
    return str(value) if value else None


def _row_parts(fields: list[str], max_chars: int) -> list[str]:
    """``column: value`` fields joined with ``; `` into pieces of at most ``max_chars`` where the
    fields allow; a single field longer than that stays whole (never cut inside a field, so
    ``"; ".join(parts)`` is exactly the row)."""
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for f in fields:
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
