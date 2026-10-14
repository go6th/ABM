"""Split a document into parts that workers read independently (owner decision 2026-10-14,
Phase 4B section 3: files of any size).

A part starts at an exact parser cursor (``research_files.parsers``) and ends at a bound, so a
part's units are the units a whole-file parse yields for that range, and no unit is read twice:

* CSV and TSV: byte ranges of about ``part_bytes`` that end at a record boundary. A boundary is a
  line end with an even number of quote characters before it (a quoted value may hold line
  breaks); the cursor carries the row number, encoding, delimiter and the header read from the
  first record, so row numbers and column names match a whole-file parse.
* Text and Markdown: byte ranges that end where the text parser ends a unit (before a Markdown
  heading, or at its unit size); the cursor carries the line number and the heading in force.
* PDF: ranges of ``pages_per_part`` pages.
* Everything else (Word, PowerPoint, Excel): one part, read with the parser's own streaming.

UTF-16 text cannot be split on a byte search for a line end, so it is one part. The planner reads
the file once, in fixed blocks; it runs inside the parser sandbox (it reads untrusted bytes).
``Bounded`` is the reader a part's parse goes through: end of file at the part's end offset.

GUARANTEE(test_parts_read_the_same_passages_as_the_whole_file)
"""

from __future__ import annotations

import codecs
import csv
import io
from dataclasses import dataclass
from typing import IO, Any

BLOCK = 1 << 20
LINE_PIECE = 8 << 20  # a longer line is read in pieces (never whole)
TEXT_KINDS = ("txt", "md")
DELIMITED = {"csv": ",", "tsv": "\t"}


@dataclass(frozen=True)
class PartPlan:
    """One part: where its parse starts and where it ends (None: the end of the file)."""

    cursor: dict[str, Any]
    end_offset: int | None = None
    end_page: int | None = None

    def to_json(self) -> dict[str, Any]:
        """JSON form (printed by the sandbox child, stored on the part row)."""
        return {"cursor": self.cursor, "end_offset": self.end_offset, "end_page": self.end_page}


def plan(
    stream: IO[bytes],
    kind: str,
    *,
    part_bytes: int,
    pages_per_part: int,
    unit_text_bytes: int = 65536,
) -> list[PartPlan]:
    """The parts of a document (see the module docstring); ``unit_text_bytes`` is the text
    parser's unit size (``research_files`` parsing config)."""
    if kind in DELIMITED:
        return _delimited(stream, DELIMITED[kind], part_bytes)
    if kind in TEXT_KINDS:
        return _text(stream, kind == "md", part_bytes, unit_text_bytes)
    if kind == "pdf":
        return _pdf(stream, pages_per_part)
    return [PartPlan({})]


def _lines(stream: IO[bytes]) -> Any:
    """The file's lines, a very long line in pieces of at most ``LINE_PIECE`` bytes (only a piece
    that ends with a line break ends a line)."""
    # LOOP(plan line pieces): cap=the file's size / LINE_PIECE pieces at least; exit=end of file
    while piece := stream.readline(LINE_PIECE):
        yield piece


def _size(stream: IO[bytes]) -> int:
    stream.seek(0, io.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


def _encoding(stream: IO[bytes]) -> str:
    from research_files.parsers.base import detect_encoding

    stream.seek(0)
    encoding = detect_encoding(stream.read(65536))
    stream.seek(0)
    return encoding


def _splittable(encoding: str) -> bool:
    """A line end is the byte ``\\n`` in this encoding (not UTF-16 or UTF-32)."""
    return codecs.lookup(encoding).name not in ("utf-16", "utf-16-le", "utf-16-be", "utf-32")


def _delimited(stream: IO[bytes], delimiter: str, part_bytes: int) -> list[PartPlan]:
    from research_files.parsers.tabular import unique_header

    size = _size(stream)
    encoding = _encoding(stream)
    if size <= part_bytes or not _splittable(encoding):
        return [PartPlan({})]
    decode_as = encoding
    # the header record: the first record, quote-aware (it may span lines)
    offset, quotes, header_lines = 0, 0, []
    lines = _lines(stream)
    for raw in lines:
        offset += len(raw)
        quotes += raw.count(b'"')
        header_lines.append(raw.decode(decode_as, errors="replace"))
        if offset > LINE_PIECE:
            return [PartPlan({})]  # no header line within 8 MiB: read whole
        if quotes % 2 == 0 and raw.endswith(b"\n"):
            break
    else:
        return [PartPlan({})]
    try:
        first = next(csv.reader(header_lines, delimiter=delimiter))
    except (StopIteration, csv.Error):
        return [PartPlan({})]
    header = unique_header([h.lstrip("﻿") for h in first])
    rows = 1
    parts = [PartPlan({})]
    target = part_bytes
    quotes = 0
    # LOOP(plan delimited parts): cap=the file's lines; exit=end of file
    for raw in lines:
        offset += len(raw)
        quotes += raw.count(b'"')
        if quotes % 2 or not raw.endswith(b"\n"):
            continue
        quotes = 0
        rows += 1
        if offset >= target and offset < size:
            parts[-1] = PartPlan(parts[-1].cursor, end_offset=offset)
            parts.append(
                PartPlan(
                    {
                        "offset": offset,
                        "row": rows,
                        "encoding": encoding,
                        "delimiter": delimiter,
                        "header": header,
                    }
                )
            )
            target = offset + part_bytes
    return parts


def _text(stream: IO[bytes], markdown: bool, part_bytes: int, unit_bytes: int) -> list[PartPlan]:
    """Text parts end exactly where the text parser ends a unit (before a Markdown heading, or
    when a unit reaches ``unit_bytes`` of text), so a part's units, and so its passages, are the
    whole-file read's."""
    from research_files.parsers.documents import _MD_HEADING

    size = _size(stream)
    encoding = _encoding(stream)
    if size <= part_bytes or not _splittable(encoding):
        return [PartPlan({})]
    parts = [PartPlan({})]
    offset, line_no, target, held = 0, 0, part_bytes, 0
    heading: str | None = None
    decode_as = encoding

    def cut(at: int, line: int, carry: str | None) -> None:
        nonlocal target
        parts[-1] = PartPlan(parts[-1].cursor, end_offset=at)
        cursor: dict[str, Any] = {"offset": at, "line": line, "encoding": encoding}
        if carry is not None:
            cursor["heading"] = carry
        parts.append(PartPlan(cursor))
        target = at + part_bytes

    pending = b""
    # LOOP(plan text parts): cap=the file's lines; exit=end of file
    for piece in _lines(stream):
        if not piece.endswith(b"\n"):
            pending += piece if len(pending) < LINE_PIECE else b""
            offset += len(piece)
            continue
        raw, pending = pending + piece, b""
        offset += len(piece)
        start = offset - len(raw)
        line_no += 1
        text = raw.decode(decode_as, errors="replace").rstrip("\r\n").lstrip("\ufeff")
        decode_as = "utf-8" if encoding == "utf-8-sig" else encoding
        match = _MD_HEADING.match(text) if markdown else None
        if match:
            # the parser ends the unit before the heading: a part may start at the heading
            if start >= target and start > 0:
                cut(start, line_no - 1, None)
            heading, held = match.group(2), 0
            continue
        held += len(text) + 1
        if held >= unit_bytes:
            held = 0
            if offset >= target and offset < size:
                cut(offset, line_no, heading)
    return parts


def _pdf(stream: IO[bytes], pages_per_part: int) -> list[PartPlan]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(stream)
        if reader.is_encrypted and not reader.decrypt(""):
            return [PartPlan({})]
        pages = len(reader.pages)
    except Exception:  # noqa: BLE001 - the parse itself reports a broken PDF
        return [PartPlan({})]
    if pages <= pages_per_part:
        return [PartPlan({})]
    return [
        PartPlan({"page": start} if start else {}, end_page=min(pages, start + pages_per_part))
        for start in range(0, pages, pages_per_part)
    ]


class Bounded(io.RawIOBase):
    """A read-only view of ``raw`` that ends at ``end`` (see the module docstring)."""

    def __init__(self, raw: IO[bytes], end: int) -> None:
        super().__init__()
        self.raw = raw
        self.end = end
        self.pos = 0
        raw.seek(0)

    def readable(self) -> bool:
        """It can be read."""
        return True

    def seekable(self) -> bool:
        """It can seek within the part."""
        return True

    def tell(self) -> int:
        """The position in the file."""
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        """Move within the bound (never past the part's end)."""
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.end}[whence]
        self.pos = max(0, min(self.end, base + offset))
        self.raw.seek(self.pos)
        return self.pos

    def readinto(self, buffer: Any) -> int:
        """Read up to the part's end; 0 there."""
        room = self.end - self.pos
        if room <= 0:
            return 0
        view = memoryview(buffer)[: min(len(buffer), room)]
        self.raw.seek(self.pos)
        got = self.raw.readinto(view)  # type: ignore[attr-defined]
        got = got or 0
        self.pos += got
        return got


def bounded(raw: IO[bytes], end: int | None) -> IO[bytes]:
    """The stream a part's parse reads (the whole file when ``end`` is None)."""
    if end is None:
        return raw
    return io.BufferedReader(Bounded(raw, end), buffer_size=BLOCK)
