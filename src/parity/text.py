"""The one text cleaner, a port of the reference's ``cl()`` (Word files) and ``clean()``
(workbooks).

The two reference cleaners differ: the Word builders replace en and em dashes, `` - ``, the
curly apostrophe and the arrow; the workbook builders replace the dashes only (the Job Postings
builder also strips posting marks first). The golden workbook cells keep `` - ``, ``’`` and
``→`` (2,116, 1,895 and 10 cells of the reference Jobs sheet), so the workbook renderers use
``clean_cell`` and the Word renderers use ``clean``. Precedence inside ``clean``: dashes, then
`` - ``, then the apostrophe, then the arrow, as the reference's chain of replacements runs.
"""

from __future__ import annotations

import re

_DASH = re.compile(r"\s*[\u2013\u2014]\s*")
_MARKS = re.compile(r"\*\*|##|#")


def clean_cell(text: object) -> str:
    """The workbook cleaner: en and em dashes (with the spaces around them) become ``, ``."""
    return _DASH.sub(", ", "" if text is None else str(text))


def clean(text: object) -> str:
    """The Word cleaner: ``clean_cell`` plus `` - `` to ``, ``, ``’`` to ``'`` and ``→`` to
    `` to ``."""
    out = clean_cell(text)
    return out.replace(" - ", ", ").replace("\u2019", "'").replace("\u2192", " to ")


def strip_posting_marks(text: object) -> str:
    """Remove ``**``, ``##`` and ``#`` from posting text, as the Job Postings builder does
    before cleaning the dashes."""
    return _MARKS.sub("", "" if text is None else str(text))


def clean_posting(text: object) -> str:
    """A posting cell: marks stripped, then the workbook cleaner."""
    return clean_cell(strip_posting_marks(text))
