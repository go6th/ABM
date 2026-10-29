"""The Word renderer contract (D-26): the ONLY input a renderer receives, on stdin, as JSON.

A ``DocumentSpec`` is a validated, self-contained description of one document: the theme's Word
section, the footer's page word and an ordered list of blocks (title, heading, paragraph,
bullets, table). It holds no database identifiers, credentials or paths. The JSON Schema in
``schema/document.schema.json`` is generated from this model (a test fails when it is stale) so
the Python and the Node side validate the same contract.

Links: a table cell is either plain text or a tuple of ``Run``; a run with an ``href`` is a
hyperlink (the owner's rule: every link in a file is a working link, with its archived copy
beside it). A paragraph with an ``href`` is one hyperlink whose text is the paragraph.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from exports.config import WordTheme

SCHEMA_VERSION = 2  # DocumentSpec.schema_version
MAX_TEXT = 40_000
_HREF = re.compile(r"^https?://\S+$")


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


SpecText = Annotated[str, Field(max_length=MAX_TEXT)]


def _href(value: str) -> str:
    if not _HREF.match(value):
        raise ValueError("a link target is an absolute http(s) address without spaces")
    return value


Href = Annotated[str, AfterValidator(_href)]


class Run(_Spec):
    """A run of text inside a cell: plain, or a hyperlink to ``href``. Newlines in the text are
    line breaks; runs follow each other inline."""

    text: SpecText
    href: Href | None = None


Cell = SpecText | tuple[Run, ...]


def cell_text(cell: Cell) -> str:
    """The visible text of a cell (runs joined as they print)."""
    return cell if isinstance(cell, str) else "".join(r.text for r in cell)


def cell_hrefs(cell: Cell) -> tuple[str, ...]:
    """The link targets of a cell, in order (none for plain text)."""
    if isinstance(cell, str):
        return ()
    return tuple(r.href for r in cell if r.href)


class TitleBlock(_Spec):
    """The document title, an optional note printed as the first line under it (what changed
    since the previous delivery) and the opening paragraph (``subtitle``)."""

    type: Literal["title"]
    text: SpecText
    subtitle: SpecText
    note: SpecText = ""


class HeadingBlock(_Spec):
    """A section heading (level 1) or sub-heading (level 2); a level 1 heading may start a new
    page (every section after the first in the reference)."""

    type: Literal["heading"]
    level: Literal[1, 2]
    text: SpecText
    page_break: bool = False

    @model_validator(mode="after")
    def _break_level(self) -> HeadingBlock:
        if self.page_break and self.level != 1:
            raise ValueError("only a level 1 heading starts a page")
        return self


class ParagraphBlock(_Spec):
    """A paragraph in one of the themed paragraph styles; with ``href`` the whole paragraph is
    one hyperlink (a source link under a Part D sub-heading)."""

    type: Literal["paragraph"]
    style: Literal["body", "quote", "note"]
    text: SpecText
    href: Href | None = None


class BulletsBlock(_Spec):
    """A bulleted list, or a numbered one starting at 1 (``numbered``). ``notes``, when given,
    holds one line per item (empty for none), printed as an indented note paragraph under its
    item without breaking the numbering (a hypothesis and its confirming question)."""

    type: Literal["bullets"]
    items: tuple[SpecText, ...] = Field(min_length=1)
    numbered: bool = False
    notes: tuple[SpecText, ...] = ()

    @model_validator(mode="after")
    def _notes(self) -> BulletsBlock:
        if self.notes and len(self.notes) != len(self.items):
            raise ValueError("one note per item, or none")
        return self


class TableBlock(_Spec):
    """A table: a header row, the theme proportions by name and the data rows. ``keys`` are the
    visible row keys (the first cell of each row, always plain text), checked by the
    row-presence verification. ``label_column`` is the column printed bold in body rows (the
    reference bolds its first column; here that is the column after the row key)."""

    type: Literal["table"]
    name: str
    headers: tuple[SpecText, ...] = Field(min_length=1)
    widths: tuple[float, ...] = Field(min_length=1)
    rows: tuple[tuple[Cell, ...], ...]
    keys: tuple[str, ...]
    label_column: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _shape(self) -> TableBlock:
        if len(self.widths) != len(self.headers):
            raise ValueError("one width per header")
        if any(len(r) != len(self.headers) for r in self.rows):
            raise ValueError("every row has one cell per header")
        if len(self.keys) != len(self.rows) or any(
            not isinstance(r[0], str) or r[0] != k
            for r, k in zip(self.rows, self.keys, strict=True)
        ):
            raise ValueError("keys are the first cell of each row, as plain text")
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("row keys are unique within a table")
        if self.label_column >= len(self.headers):
            raise ValueError("the label column is one of the table's columns")
        return self


Block = Annotated[
    TitleBlock | HeadingBlock | ParagraphBlock | BulletsBlock | TableBlock,
    Field(discriminator="type"),
]


class DocumentSpec(_Spec):
    """One Word document, fully described. The footer is the page word and a page number field
    (the reference's only footer). The header part exists only for a tenant's white-label brand
    name (``header_text``); the reference has none."""

    schema_version: Literal[2] = 2
    theme: WordTheme
    page_label: SpecText
    header_text: SpecText | None = None
    blocks: tuple[Block, ...] = Field(min_length=1)


def json_schema() -> str:
    """The generated JSON Schema (``word/schema/document.schema.json``)."""
    schema = DocumentSpec.model_json_schema()
    schema["$comment"] = "Generated from exports.word.contract.DocumentSpec; do not edit."
    return json.dumps(schema, indent=1, sort_keys=True) + "\n"
