"""The shape of a parsed unit decides its route, never the file type it came from (data access,
part B2):

* ``tabular``: the unit carries rows and a header (a sheet, a CSV, a JSON array of records).
* ``transcript``: its text is mostly timestamped speaker turns (a call transcript, a WebVTT or
  SRT caption file).
* ``prose``: everything else (a page, a slide, a section, plain text).

Only prose is chunked for embedding; tables go to the table store (``documents.tables``) and a
slim row passage; transcripts keep their window chunking. ``ShapeLog`` logs the decision once
per member (the ``documents.shape`` event, identifiers and the shape only — document and member
NAMES are never logged, telemetry contract D-56) so an ingest report says how each file was
read. ``documents.chunking`` routes every unit through this module.

WebVTT and SRT cue blocks (``00:00:01.000 --> 00:00:04.000`` then the caption, with ``<v Name>``
or ``Name:`` naming the speaker) are normalised to ``[h:mm:ss] Speaker: text`` lines first, so a
caption file is a transcript like any other.

GUARANTEE(test_units_route_by_shape_not_by_file_type)
"""

from __future__ import annotations

import re
from typing import Any, Literal

from documents.sandbox import ParsedUnit
from obs.logging import logger

Shape = Literal["tabular", "transcript", "prose"]
Turn = tuple[float, str, str, int]  # (seconds, speaker, text, line number)
_log = logger(__name__)

_TURN = re.compile(
    r"^\s*\[?(?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)\]?\s*[-–]?\s*"
    r"(?P<speaker>[A-Z][\w .'&-]{0,40}?)\s*:\s*(?P<text>.*)$"
)
_TURN_AFTER = re.compile(
    r"^\s*(?P<speaker>[A-Z][\w .'&-]{0,40}?)\s*[\[(](?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})[\])]\s*:?\s*"
    r"(?P<text>.*)$"
)
_CUE = re.compile(
    r"^\s*(?P<start>(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*"
    r"(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}"
)
_VOICE = re.compile(r"^\s*<v\s+(?P<speaker>[^>]{1,60})>\s*(?P<text>.*?)\s*(?:</v>)?\s*$")
_SPEAKER_LINE = re.compile(r"^\s*(?P<speaker>[A-Z][\w .'&-]{0,40}?)\s*:\s*(?P<text>.+)$")
_TAG = re.compile(r"</?[a-z][^>]*>")


def seconds(ts: str) -> float:
    """``h:mm:ss`` or ``mm:ss`` (with an optional fraction, comma or dot) to seconds."""
    parts = [float(p.replace(",", ".")) for p in ts.split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def is_cue_text(text: str) -> bool:
    """True when ``text`` is a WebVTT or SRT caption file (a header or cue arrows)."""
    head = text.lstrip()[:2000]
    return head.upper().startswith("WEBVTT") or bool(
        _CUE.search(head) and len(_CUE.findall(text)) >= 2
    )


def cues_to_turns(text: str) -> str:
    """Caption cue blocks as ``[start] Speaker: text`` lines (one per cue, speaker ``Speaker``
    when the cue names none); other lines are left as they are."""
    out: list[str] = []
    start: str | None = None
    for line in text.splitlines():
        cue = _CUE.match(line)
        if cue:
            start = cue.group("start")
            continue
        if start is None:
            if line.strip().isdigit() or line.strip().upper() in ("", "WEBVTT"):
                continue  # an SRT sequence number and the VTT header are not transcript text
            out.append(line)
            continue
        if not line.strip():
            start = None
            continue
        voice = _VOICE.match(line)
        if voice:
            speaker, body = voice.group("speaker"), voice.group("text")
        else:
            said = _SPEAKER_LINE.match(line)
            speaker, body = (
                (said.group("speaker"), said.group("text")) if said else ("Speaker", line)
            )
        body = _TAG.sub("", body).strip()
        if body:
            out.append(f"[{start}] {speaker.strip()}: {body}")
    return "\n".join(out)


def turns(text: str) -> list[Turn] | None:
    """Timestamped speaker turns ``(seconds, speaker, text, line)`` when the text is a transcript
    (at least 60% of its non-empty lines, and at least 4 turns), else None. A caption file is
    normalised with ``cues_to_turns`` first."""
    if is_cue_text(text):
        text = cues_to_turns(text)
    found: list[Turn] = []
    nonempty = 0
    for no, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.strip().upper() == "WEBVTT":
            continue
        nonempty += 1
        m = _TURN.match(line) or _TURN_AFTER.match(line)
        if m:
            found.append((seconds(m.group("ts")), m.group("speaker").strip(), m.group("text"), no))
        elif found:
            # a continuation line belongs to the previous turn
            t = found[-1]
            found[-1] = (t[0], t[1], f"{t[2]} {line.strip()}", t[3])
            nonempty -= 1
    if len(found) >= 4 and len(found) >= 0.6 * max(nonempty, 1):
        return found
    return None


def shape_of(unit: ParsedUnit) -> Shape:
    """The shape of one unit (see the module docstring)."""
    if unit.rows is not None:
        return "tabular"
    if unit.text.strip() and turns(unit.text) is not None:
        return "transcript"
    return "prose"


def member_of(location: dict[str, Any]) -> str | None:
    """The file inside an archive a location belongs to, or None for the document itself."""
    start = location.get("from", location)
    value = start.get("member") or location.get("member")
    return str(value) if value else None


class ShapeLog:
    """Logs the shape decision once per member of a document (the ``documents.shape`` event) and
    keeps the decisions for the ingest report. Names never leave this object: the log line
    carries the tenant, the document's detected type and the shape only (D-56)."""

    def __init__(self, tenant_id: str, kind: str = "") -> None:
        """A log for one document of ``tenant_id`` whose detected type is ``kind``."""
        self.tenant_id = tenant_id
        self.kind = kind
        self.decided: dict[str, Shape] = {}

    def note(self, unit: ParsedUnit, shape: Shape) -> Shape:
        """Record the shape of the member ``unit`` belongs to, logging it on first sight, and
        answer the recorded shape (a member keeps its first decision: one file, one route)."""
        member = member_of(unit.location) or ""
        if member in self.decided:
            return self.decided[member]
        self.decided[member] = shape
        _log.info(
            "documents.shape",
            extra={"tenant_id": self.tenant_id, "kind": self.kind, "shape": shape},
        )
        return shape
