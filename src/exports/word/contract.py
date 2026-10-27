"""The Word renderer contract (D-26): the ONLY input a renderer receives, on stdin, as JSON.

A ``DocumentSpec`` is a validated, self-contained description of one document: the theme's Word
section, the header and footer text and an ordered list of blocks (title, heading, paragraph,
bullets, table). It holds no database identifiers, credentials or paths. The JSON Schema in
``schema/document.schema.json`` is generated from this model (a test fails when it is stale) so
the Python and the Node side validate the same contract.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from exports.config import WordTheme

SCHEMA_VERSION = 1  # DocumentSpec.schema_version
MAX_TEXT = 40_000


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


SpecText = Annotated[str, Field(max_length=MAX_TEXT)]


class TitleBlock(_Spec):
    """The document title and its subtitle line."""

    type: Literal["title"]
    text: SpecText
    subtitle: SpecText


class HeadingBlock(_Spec):
    """A section heading (level 1) or sub-heading (level 2)."""

    type: Literal["heading"]
    level: Literal[1, 2]
    text: SpecText


class ParagraphBlock(_Spec):
    """A paragraph in one of the themed paragraph styles."""

    type: Literal["paragraph"]
    style: Literal["body", "quote", "note"]
    text: SpecText


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
    visible row keys (the first cell of each row), checked by the row-presence verification."""

    type: Literal["table"]
    name: str
    headers: tuple[SpecText, ...] = Field(min_length=1)
    widths: tuple[float, ...] = Field(min_length=1)
    rows: tuple[tuple[SpecText, ...], ...]
    keys: tuple[str, ...]

    @model_validator(mode="after")
    def _shape(self) -> TableBlock:
        if len(self.widths) != len(self.headers):
            raise ValueError("one width per header")
        if any(len(r) != len(self.headers) for r in self.rows):
            raise ValueError("every row has one cell per header")
        if len(self.keys) != len(self.rows) or any(
            r[0] != k for r, k in zip(self.rows, self.keys, strict=True)
        ):
            raise ValueError("keys are the first cell of each row")
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("row keys are unique within a table")
        return self


Block = Annotated[
    TitleBlock | HeadingBlock | ParagraphBlock | BulletsBlock | TableBlock,
    Field(discriminator="type"),
]


class DocumentSpec(_Spec):
    """One Word document, fully described."""

    schema_version: Literal[1] = 1
    theme: WordTheme
    header_text: SpecText
    footer_text: SpecText
    blocks: tuple[Block, ...] = Field(min_length=1)


def json_schema() -> str:
    """The generated JSON Schema (``word/schema/document.schema.json``)."""
    schema = DocumentSpec.model_json_schema()
    schema["$comment"] = "Generated from exports.word.contract.DocumentSpec; do not edit."
    return json.dumps(schema, indent=1, sort_keys=True) + "\n"
