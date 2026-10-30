"""Every link a client file carries is a plain http(s) address (security review 2026-10-29, F10):
the render models, the Word contract and the workbook writer share one check
(``exports.urlcheck``): no whitespace anywhere (a trailing newline included), no credentials,
no control, zero-width or bidi characters, no backslash, http or https only, 2,048 characters at
most. SYNTHETIC addresses only."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from exports.config import Column, load_export_config
from exports.errors import SchemaViolation
from exports.excel.common import Book
from exports.models import Url
from exports.urlcheck import MAX_URL, is_safe_http_url
from exports.word.contract import Href

GOOD = [
    "https://a.example/x",
    "http://a.example/",
    "https://a.example/path?q=1&r=2#frag",
    "https://sub.a.example:8443/x",
    "https://a.example/café",
]
BAD = [
    "https://a.example/x\n",  # `$` matches before a trailing newline
    "https://a.example/x\r\n",
    "https://a.example/x y",
    "https://a.example/\tx",
    "https://user:pw@a.example/",
    "https://user@a.example/",
    "https://a.example/‮evil",  # right-to-left override
    "https://a.example/​x",  # zero-width space
    "https://a.example/\x00x",
    "https://a.example\\x",
    "javascript:alert(1)",
    "data:text/html,<b>x</b>",
    "file:///etc/passwd",
    "ftp://a.example/x",
    "//a.example/x",
    "https://",
    "https:///x",
    "/relative/path",
    "",
    "https://a.example/" + "x" * MAX_URL,
]


@pytest.mark.parametrize("url", GOOD)
def test_a_plain_http_address_passes_everywhere(url: str) -> None:
    assert is_safe_http_url(url)
    assert TypeAdapter(Url).validate_python(url) == url
    assert TypeAdapter(Href).validate_python(url) == url


@pytest.mark.parametrize("url", BAD)
def test_anything_else_is_refused_by_the_models_and_the_word_contract(url: str) -> None:
    assert not is_safe_http_url(url)
    with pytest.raises(ValidationError):
        TypeAdapter(Url).validate_python(url)
    with pytest.raises(ValidationError):
        TypeAdapter(Href).validate_python(url)


@pytest.mark.parametrize("url", BAD)
def test_the_workbook_writer_refuses_a_bad_hyperlink_target(url: str) -> None:
    cfg = load_export_config()
    book = Book(cfg)
    ws = book.sheet("Links")
    columns = [Column(key="url", header="LinkedIn", kind="url", link_text="Open profile")]
    with pytest.raises(SchemaViolation):
        book.link(ws, 2, 1, "Open", url)
    if url:  # an empty address is no link at all (an empty cell), not a bad one
        with pytest.raises(SchemaViolation):
            book.body(ws, columns, {"url": url}, 3, "P-0001")


def test_the_workbook_writer_writes_a_good_hyperlink() -> None:
    cfg = load_export_config()
    book = Book(cfg)
    ws = book.sheet("Links")
    book.link(ws, 2, 1, "Open", "https://a.example/x")
    assert ws.cell(row=2, column=1).hyperlink.target == "https://a.example/x"
