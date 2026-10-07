"""VW4 safe rendering: a corpus of hostile payloads (HTML and script injection, markdown HTML,
URL schemes, spreadsheet formulas, control and direction-override characters) and properties
over arbitrary text. Pure."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from views.render_safe import csv_cell, html_text, safe_link, sheet_cell, text_cell

RLO, LRI, ZWJ, NUL = chr(0x202E), chr(0x2066), chr(0x200D), chr(0)
HTML_PAYLOADS = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "<svg/onload=alert(1)>",
    "[x](javascript:alert(1))",
    "<a href='javascript:alert(1)'>x</a>",
    "</textarea><script>fetch('//evil')</script>",
    "<iframe srcdoc='<script>1</script>'>",
    "{{constructor.constructor('alert(1)')()}}",
]
FORMULAS = [
    "=1+1",
    "+cmd|' /C calc'!A0",
    "-2+3",
    "@SUM(A1)",
    '=HYPERLINK("http://evil","x")',
    "\t=1",
    "\r=1",
    "  =1",
    chr(0xFF1D) + "1",
    chr(0xA0) + "=1",
]
BAD_LINKS = [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    " javascript:alert(1)",
    "data:text/html,x",
    "vbscript:x",
    "file:///etc/passwd",
    "//evil.test/x",
    "/relative",
    "https://u:p@evil.test/",
    "https:///nohost",
    "http://evil.test/a b",
    "https://evil.test\\@good.test",
    "",
    "x" * 3000,
    "https://evil.test/" + chr(10) + "x",
]


@pytest.mark.parametrize("payload", HTML_PAYLOADS)
def test_html_payloads_render_as_inert_text(payload: str) -> None:
    out = html_text(payload)
    assert "<" not in out and ">" not in out
    assert html_text(payload).count("&lt;") == payload.count("<")


@pytest.mark.parametrize("payload", FORMULAS)
def test_spreadsheet_formulas_are_neutralised(payload: str) -> None:
    for cell in (csv_cell(payload), sheet_cell(payload)):
        assert isinstance(cell, str) and cell.startswith("'"), (payload, cell)


@pytest.mark.parametrize("link", BAD_LINKS)
def test_only_plain_http_links_survive(link: str) -> None:
    assert safe_link(link) is None, link


def test_good_links_and_typed_cells() -> None:
    assert safe_link("https://acme.example/careers?x=1") == "https://acme.example/careers?x=1"
    assert safe_link("http://acme.example") == "http://acme.example"
    assert safe_link(42) is None
    assert csv_cell(None) == "" and csv_cell(True) == "true" and csv_cell(False) == "false"
    assert csv_cell(3) == "3" and csv_cell(2.5) == "2.5"
    assert sheet_cell(None) is None and sheet_cell(3) == 3 and sheet_cell(True) is True
    assert csv_cell("plain text") == "plain text"


def test_control_and_direction_characters_are_removed() -> None:
    assert text_cell(f"admin{RLO}gnp.exe") == "admingnp.exe"
    assert text_cell(f"a{LRI}b{NUL}c") == "abc"
    assert text_cell("line one\nline\ttwo\r\n") == "line one line two"
    assert text_cell(f"family{ZWJ}emoji") == f"family{ZWJ}emoji"  # joiners keep emoji intact
    assert text_cell(None) == ""
    assert text_cell("x" * 50, cap=10) == "x" * 9 + chr(0x2026)


@given(st.text(max_size=300))
def test_render_safe_neutralises_every_payload_in_the_corpus(value: str) -> None:
    """Over arbitrary text: no control or override character survives, HTML output has no raw
    angle bracket or quote, and no CSV cell can start a formula."""
    cleaned = text_cell(value)
    assert not any(ord(c) < 32 for c in cleaned)
    assert not set(cleaned) & {RLO, LRI}
    out = html_text(value)
    assert "<" not in out and ">" not in out and '"' not in out
    cell = csv_cell(value)
    assert not cell.lstrip(" " + chr(0xA0)).startswith(("=", "+", "-", "@")) or cell.startswith("'")
