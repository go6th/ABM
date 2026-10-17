"""Shape routing (documents/shapes.py, the data access brief's B2): a parsed unit is routed by
its shape — rows, timestamped speaker turns, or prose — never by the file type it came from. A
WebVTT or SRT caption file is a transcript: its cue blocks normalise to ``[start] Speaker:
text`` lines and chunk to transcript windows. The decision is logged once per member
(``documents.shape``), without document or member names (telemetry contract D-56).
"""

from __future__ import annotations

import logging

from documents.chunking import Chunker
from documents.config import load_config
from documents.redact import Redactor
from documents.sandbox import ParsedUnit
from documents.shapes import ShapeLog, cues_to_turns, seconds, shape_of
from tests.documents import fixtures

CFG = load_config()

VTT = """WEBVTT - the discovery call

1
00:00:01.000 --> 00:00:04.500
<v Buyer>We replaced the vendor in the last quarter.</v>

2
00:00:18.000 --> 00:00:21.000
Seller: what mattered most in that decision?

00:01:00.250 --> 00:01:04.000
<v Buyer>The <i>price</i> and the support hours.</v>

00:01:19.500 --> 00:01:23.000
Seller: noted, thank you.
"""


def test_units_route_by_shape_not_by_file_type(caplog) -> None:
    """Rows are tabular, turns are a transcript, everything else is prose — whatever the name says;
    caption cues become turns, and the decision is logged once per member."""
    rows = ParsedUnit(
        "rows",
        {"table": "Contacts", "member": "notes.md"},
        "",
        header=["Name"],
        rows=[(2, ["Lee Park"])],
    )
    assert shape_of(rows) == "tabular"  # a member called .md still routes to the table store

    plain = ParsedUnit("section", {"section": "Overview"}, "A plain paragraph. And another.\n" * 6)
    assert shape_of(plain) == "prose"

    turns = ParsedUnit("section", {"section": "(no heading)"}, fixtures.transcript_txt().decode())
    assert shape_of(turns) == "transcript"

    caption = ParsedUnit("page", {"page": 1}, VTT)
    assert shape_of(caption) == "transcript"  # a WebVTT page is a transcript, not prose

    normalised = cues_to_turns(VTT)
    assert "[00:00:01.000] Buyer: We replaced the vendor in the last quarter." in normalised
    assert "[00:00:18.000] Seller: what mattered most in that decision?" in normalised
    assert "[00:01:00.250] Buyer: The price and the support hours." in normalised  # tags stripped
    assert seconds("01:02:03.500") == 3723.5 and seconds("02:03") == 123.0

    # chunking follows the shape too: the caption file becomes transcript windows, speakers kept
    log = ShapeLog("tenant-x", "vtt")
    chunker = Chunker(CFG.chunking, "call.vtt", Redactor("standard"), 100, shape_log=log)
    with caplog.at_level(logging.INFO, "documents.shapes"):
        windows = list(chunker.passages([caption]))
    assert windows and all(w.kind == "transcript_window" for w in windows)
    assert "Buyer: We replaced the vendor in the last quarter." in windows[0].text
    assert windows[0].location["start_s"] == 1.0
    assert log.decided == {"": "transcript"}

    # a second unit of the same member logs nothing new; another member is its own decision
    other = ParsedUnit("note", {"member": "readme.txt"}, "Notes.\n" * 4)
    with caplog.at_level(logging.INFO, "documents.shapes"):
        list(chunker.passages([ParsedUnit("page", {"page": 2}, VTT), other, other], final=True))
    assert log.decided == {"": "transcript", "readme.txt": "prose"}
    events = [r for r in caplog.records if r.msg == "documents.shape"]
    assert len(events) == 2  # one per member over the whole document, never per unit
    assert {getattr(e, "shape", None) for e in events} == {"transcript", "prose"}
