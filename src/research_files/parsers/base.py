"""The contract every parser plugin follows.

A parser turns a seekable binary stream into ``Unit`` objects, in order, starting at a resume
``cursor`` (``{}`` for the start). Each unit carries the cursor to resume AFTER it, so the
pipeline can store the unit, its findings and the cursor in one transaction and continue from
exactly there after any stop. A parser never holds the whole file: it reads a bounded amount per
unit. A problem inside the file is a ``Finding`` (error for corrupt or truncated content); a
parser stops at a fatal problem by yielding a last unit with ``fatal=True``.

GUARANTEE(test_every_parser_resumes_to_the_same_units)
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import IO, Any, Protocol

from research_files.checks import Finding
from research_files.config import ResearchFilesConfig


@dataclass
class Unit:
    """One stored chunk of parsed output (see the module docstring)."""

    kind: str  # rows | page | slide | section | text | image | media | note
    location: dict[str, Any]
    text: str
    cursor: dict[str, Any]
    findings: list[Finding] = field(default_factory=list)
    table: str | None = None  # profiling key for tabular units
    header: list[str] | None = None  # the table's header, on its first unit
    header_row: int | None = None
    rows: list[tuple[int, list[Any]]] | None = None  # (source row number, cells)
    heading: str | None = None  # document section heading (None: no heading)
    stats: dict[str, Any] = field(default_factory=dict)
    fatal: bool = False


@dataclass(frozen=True)
class ParseContext:
    """What a parser may know: the config, the display name and the archive depth."""

    cfg: ResearchFilesConfig
    name: str
    depth: int = 0


class Parser(Protocol):
    """``units(stream, cursor, ctx)`` (see the module docstring)."""

    def units(self, stream: IO[bytes], cursor: dict[str, Any], ctx: ParseContext) -> Iterator[Unit]:
        """Units from ``cursor`` on."""
        ...


def fatal(kind: str, location: dict[str, Any], check: str, message: str, fix: str) -> Unit:
    """A last unit that records a fatal problem (the units before it are kept)."""
    return Unit(
        kind="note",
        location=location,
        text="",
        cursor={"done": True},
        findings=[Finding("error", check, location, message, fix)],
        fatal=True,
    )


def detect_encoding(sample: bytes) -> str:
    """The text encoding of a sample (UTF-8 when unsure)."""
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    try:
        sample.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as error:
        if error.start >= len(sample) - 3:
            return "utf-8"
    from charset_normalizer import from_bytes

    best = from_bytes(sample[:65536]).best()
    return best.encoding if best is not None else "utf-8"
