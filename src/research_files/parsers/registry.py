"""The plugin for each detected type (None: stored, not parsed)."""

from __future__ import annotations

from research_files.detect import ARCHIVE, IMAGE, MEDIA
from research_files.parsers.base import Parser


def parser_for(kind: str) -> Parser | None:
    """See the module docstring."""
    from research_files.parsers import archives, documents, media, tabular

    simple: dict[str, Parser] = {
        "csv": tabular.DelimitedParser(","),
        "tsv": tabular.DelimitedParser("\t"),
        "xlsx": tabular.XlsxParser(),
        "parquet": tabular.ParquetParser(),
        "json": tabular.JsonParser(),
        "jsonl": tabular.JsonLinesParser(),
        "pdf": documents.PdfParser(),
        "docx": documents.DocxParser(),
        "pptx": documents.PptxParser(),
        "txt": documents.TextParser(markdown=False),
        "md": documents.TextParser(markdown=True),
        "html": documents.HtmlParser(),
    }
    if kind in simple:
        return simple[kind]
    if kind in IMAGE:
        return media.ImageParser()
    if kind in MEDIA:
        return media.MediaParser()
    if kind in ARCHIVE:
        return archives.ArchiveParser(kind, parser_for)
    return None
