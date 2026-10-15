"""Document parsers: PDF (pages), DOCX (sections by heading, tables as rows of text), PPTX
(slides), plain text and Markdown (sections by heading), HTML (sections by heading, streamed).

A unit is one page, slide or section (a long section is split at ``unit_text_bytes``). A heading
followed by no text is an empty section (the pipeline reports it). A scanned page (no text but an
image) is reported: no OCR engine is installed, so its text is not available.

Cursors: PDF ``page``; DOCX ``block`` (body elements done); PPTX ``slide``; text ``offset`` and
``line``; HTML ``element`` (block elements done).

GUARANTEE(test_every_parser_resumes_to_the_same_units)
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterator
from typing import IO, Any

from research_files.checks import Finding
from research_files.parsers.base import ParseContext, Unit, detect_encoding, fatal

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
PML = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
ODR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
# Word parts outside the body that people write in (read after the body, 2026-10-15)
DOCX_EXTRAS: tuple[tuple[str, str], ...] = (
    ("Headers and footers", r"word/(header|footer)\d*\.xml$"),
    ("Footnotes and endnotes", r"word/(footnotes|endnotes)\.xml$"),
    ("Comments", r"word/comments\.xml$"),
)


def _size(stream: IO[bytes]) -> int:
    here = stream.tell()
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(here)
    return size


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, start = [], 0
    # LOOP(section split): cap=len(text) / limit parts; exit=text consumed
    while start < len(text):
        end = min(len(text), start + limit)
        cut = text.rfind("\n", start, end)
        if cut <= start or end == len(text):
            cut = end
        parts.append(text[start:cut])
        start = cut
    return parts


class PdfParser:
    """One unit per page (``pypdf``)."""

    def units(self, stream: IO[bytes], cursor: dict[str, Any], ctx: ParseContext) -> Iterator[Unit]:
        """See the module docstring."""
        if cursor.get("done"):
            return
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        try:
            reader = PdfReader(stream)
            if reader.is_encrypted and not reader.decrypt(""):
                yield fatal(
                    "note",
                    {},
                    "encrypted_file",
                    "The PDF is password protected; its text cannot be read.",
                    "Upload a copy without a password.",
                )
                return
            pages = len(reader.pages)
        except (PdfReadError, ValueError, KeyError, OSError) as error:
            yield fatal(
                "note",
                {},
                "corrupt_file",
                f"The PDF cannot be opened ({type(error).__name__}); it may be corrupt or"
                " truncated.",
                "Re-export the PDF and upload it again.",
            )
            return
        start = int(cursor.get("page", 0))
        for index in range(start, pages):
            page_no = index + 1
            findings: list[Finding] = []
            try:
                page = reader.pages[index]
                text = page.extract_text() or ""
                resources: Any = page.get("/Resources")
                has_images = bool(resources) and "/XObject" in resources
            except Exception as error:  # noqa: BLE001 - pypdf raises many types on bad pages
                findings.append(
                    Finding(
                        "error",
                        "corrupt_page",
                        {"page": page_no},
                        f"Page {page_no} cannot be read ({type(error).__name__}).",
                        "Re-export the PDF and upload it again.",
                    )
                )
                text, has_images = "", False
            if not text.strip() and has_images:
                findings.append(
                    Finding(
                        "warn",
                        "scanned_page_no_ocr",
                        {"page": page_no},
                        f"Page {page_no} looks scanned (an image without text). No OCR engine is"
                        " installed, so its text is not included.",
                        "Upload a PDF with a text layer, or ask an operator to enable OCR.",
                    )
                )
            yield Unit(
                "page",
                {"page": page_no},
                text,
                {"page": page_no},
                findings,
                heading=f"Page {page_no}",
                stats={"words": len(text.split()), "scanned": not text.strip() and has_images},
            )
        yield Unit("note", {}, "", {"done": True}, stats={"pages": pages})


def _docx_text(element: Any) -> str:
    return "".join(t.text or "" for t in element.iter(f"{W}t"))


def _docx_heading(paragraph: Any) -> str | None:
    style = paragraph.find(f"{W}pPr/{W}pStyle")
    if style is None:
        return None
    value = str(style.get(f"{W}val") or "")
    if value.lower().startswith(("heading", "title")):
        return value
    return None


class DocxParser:
    """Sections of a Word document, streamed with ``lxml.etree.iterparse``."""

    def units(self, stream: IO[bytes], cursor: dict[str, Any], ctx: ParseContext) -> Iterator[Unit]:
        """See the module docstring."""
        if cursor.get("done"):
            return
        from lxml import etree

        try:
            archive = zipfile.ZipFile(stream)
            body = archive.open("word/document.xml")
        except (zipfile.BadZipFile, KeyError, OSError) as error:
            yield fatal(
                "note",
                {},
                "corrupt_file",
                f"The document cannot be opened ({type(error).__name__}).",
                "Open and save the document in a word processor, then upload it again.",
            )
            return
        skip = int(cursor.get("block", 0))
        skip_parts = int(cursor.get("skip_parts", 0))
        limit = ctx.cfg.parsing.unit_text_bytes
        block = 0
        heading: str | None = None
        lines: list[str] = []
        section_start = skip + 1
        try:
            for _, element in etree.iterparse(body, events=("end",), tag=(f"{W}p", f"{W}tbl")):
                parent = element.getparent()
                if parent is None or parent.tag != f"{W}body":
                    continue
                block += 1
                if block <= skip:
                    element.clear()
                    continue
                if element.tag == f"{W}tbl":
                    for row in element.iter(f"{W}tr"):
                        cells = [_docx_text(c) for c in row.iter(f"{W}tc")]
                        lines.append(" | ".join(cells))
                    element.clear()
                    continue
                text = _docx_text(element)
                level = _docx_heading(element)
                element.clear()
                if level is not None:
                    if heading is not None or lines:
                        yield from self._section(
                            heading, lines, section_start, block - 1, limit, skip_parts
                        )
                        skip_parts = 0
                    heading, lines, section_start = text.strip() or level, [], block
                    continue
                if text.strip():
                    lines.append(text)
        except etree.XMLSyntaxError as error:
            yield fatal(
                "note",
                {"block": block},
                "truncated_or_corrupt",
                f"The document body is damaged after block {block} ({str(error)[:120]}).",
                "Open and save the document again, then upload it.",
            )
            return
        if heading is not None or lines:
            yield from self._section(heading, lines, section_start, block, limit, skip_parts)
        yield from self._extras(archive, block, int(cursor.get("extra", 0)), limit)
        yield Unit("note", {}, "", {"done": True})

    @staticmethod
    def _extras(archive: zipfile.ZipFile, block: int, done: int, limit: int) -> Iterator[Unit]:
        """Headers, footers, footnotes, endnotes and comments, one section each (they were
        dropped before, audit 2026-10-15); boilerplate repeated across headers is kept once."""
        from lxml import etree

        names = archive.namelist()
        for index, (title, pattern) in enumerate(DOCX_EXTRAS, start=1):
            if index <= done:
                continue
            seen: list[str] = []
            for name in sorted(n for n in names if re.match(pattern, n)):
                try:
                    root = etree.fromstring(archive.read(name))
                except (etree.XMLSyntaxError, KeyError, zipfile.BadZipFile):
                    continue
                for para in root.iter(f"{W}p"):
                    text = _docx_text(para).strip()
                    if text and text not in seen:
                        seen.append(text)
            text = "\n".join(seen)
            if not text:
                continue
            for i, part in enumerate(_split(text, limit)):
                yield Unit(
                    "section",
                    {"section": title, "blocks": [block, block], "part": i + 1},
                    part,
                    {"block": block, "extra": index},
                    heading=title if i == 0 else None,
                    stats={"words": len(part.split()), "empty": False, "part": i + 1},
                )

    @staticmethod
    def _section(
        heading: str | None, lines: list[str], first: int, last: int, limit: int, skip_parts: int
    ) -> Iterator[Unit]:
        """The section's units. A split section's parts carry a cursor that re-reads the section
        from its first block and skips the parts already stored."""
        text = "\n".join(lines)
        parts = _split(text, limit)
        for i, part in enumerate(parts):
            if i < skip_parts:
                continue
            final = i == len(parts) - 1
            yield Unit(
                "section",
                {"section": heading or "(no heading)", "blocks": [first, last], "part": i + 1},
                part,
                {"block": last} if final else {"block": first - 1, "skip_parts": i + 1},
                heading=heading if i == 0 else None,
                stats={"words": len(part.split()), "empty": not text.strip(), "part": i + 1},
            )


class PptxParser:
    """One unit per slide (the slide's text in reading order)."""

    def units(self, stream: IO[bytes], cursor: dict[str, Any], ctx: ParseContext) -> Iterator[Unit]:
        """See the module docstring."""
        if cursor.get("done"):
            return
        from lxml import etree

        try:
            archive = zipfile.ZipFile(stream)
            names = [n for n in archive.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        except (zipfile.BadZipFile, OSError) as error:
            yield fatal(
                "note",
                {},
                "corrupt_file",
                f"The presentation cannot be opened ({type(error).__name__}).",
                "Open and save the presentation again, then upload it.",
            )
            return
        names = _slide_order(archive, names)
        start = int(cursor.get("slide", 0))
        for index in range(start, len(names)):
            slide = index + 1
            try:
                root = etree.fromstring(archive.read(names[index]))
            except (etree.XMLSyntaxError, KeyError, zipfile.BadZipFile) as error:
                yield Unit(
                    "slide",
                    {"slide": slide},
                    "",
                    {"slide": slide},
                    [
                        Finding(
                            "error",
                            "corrupt_slide",
                            {"slide": slide},
                            f"Slide {slide} cannot be read ({type(error).__name__}).",
                            "Open and save the presentation again.",
                        )
                    ],
                )
                continue
            paragraphs = []
            for para in root.iter(f"{A}p"):
                text = "".join(t.text or "" for t in para.iter(f"{A}t")).strip()
                if text:
                    paragraphs.append(text)
            title = paragraphs[0][:200] if paragraphs else None
            notes = _slide_notes(archive, names[index])
            body = "\n".join(paragraphs + ([f"Speaker notes: {notes}"] if notes else []))
            yield Unit(
                "slide",
                {"slide": slide},
                body,
                {"slide": slide},
                heading=title or f"Slide {slide}",
                stats={"words": len(body.split()), "empty": not body.strip()},
            )
        yield Unit("note", {}, "", {"done": True}, stats={"slides": len(names)})


def _rels(archive: zipfile.ZipFile, part: str) -> dict[str, str]:
    """A part's relationships: id -> target path inside the package."""
    from lxml import etree

    folder, name = part.rsplit("/", 1)
    try:
        root = etree.fromstring(archive.read(f"{folder}/_rels/{name}.rels"))
    except (KeyError, etree.XMLSyntaxError, zipfile.BadZipFile):
        return {}
    out = {}
    for rel in root.iter(f"{PKG_REL}Relationship"):
        target = str(rel.get("Target") or "")
        path = target.lstrip("/") if target.startswith("/") else _join(folder, target)
        out[str(rel.get("Id"))] = path
    return out


def _join(folder: str, target: str) -> str:
    parts = folder.split("/")
    for piece in target.split("/"):
        if piece == "..":
            parts.pop()
        elif piece and piece != ".":
            parts.append(piece)
    return "/".join(parts)


def _slide_order(archive: zipfile.ZipFile, names: list[str]) -> list[str]:
    """Slides in the deck's own order (presentation.xml sldIdLst), not by file name; slides the
    list does not name follow in file order. Falls back to file order."""
    from lxml import etree

    by_number = sorted(names, key=lambda n: int(re.findall(r"\d+", n)[-1]))
    try:
        root = etree.fromstring(archive.read("ppt/presentation.xml"))
    except (KeyError, etree.XMLSyntaxError, zipfile.BadZipFile):
        return by_number
    rels = _rels(archive, "ppt/presentation.xml")
    ordered = [
        rels[str(s.get(f"{ODR}id"))]
        for s in root.iter(f"{PML}sldId")
        if rels.get(str(s.get(f"{ODR}id"))) in names
    ]
    return ordered + [n for n in by_number if n not in ordered]


def _slide_notes(archive: zipfile.ZipFile, slide: str) -> str:
    """The speaker notes of one slide (the notes page's text boxes, without the slide image or
    number placeholders), or "" when it has none."""
    from lxml import etree

    note = next((t for t in _rels(archive, slide).values() if "notesSlide" in t), None)
    if note is None:
        return ""
    try:
        root = etree.fromstring(archive.read(note))
    except (KeyError, etree.XMLSyntaxError, zipfile.BadZipFile):
        return ""
    out = []
    for shape in root.iter(f"{PML}sp"):
        kind = shape.find(f".//{PML}ph")
        if kind is not None and kind.get("type") in ("sldImg", "sldNum", "hdr", "ftr", "dt"):
            continue
        text = " ".join(
            "".join(t.text or "" for t in p.iter(f"{A}t")).strip() for p in shape.iter(f"{A}p")
        ).strip()
        if text:
            out.append(text)
    return " ".join(out)


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*#*\s*$")


class TextParser:
    """Plain text and Markdown, streamed by line (Markdown headings start sections)."""

    def __init__(self, markdown: bool) -> None:
        self.markdown = markdown

    def units(self, stream: IO[bytes], cursor: dict[str, Any], ctx: ParseContext) -> Iterator[Unit]:
        """See the module docstring."""
        if cursor.get("done"):
            return
        notes: list[Finding] = []
        if "encoding" not in cursor:
            stream.seek(0)
            cursor = {"offset": 0, "line": 0, "encoding": detect_encoding(stream.read(65536))}
            from research_files.parsers.tabular import encoding_finding

            notes = encoding_finding(cursor["encoding"], {})
        encoding = cursor["encoding"]
        offset, line_no = int(cursor["offset"]), int(cursor["line"])
        heading: str | None = cursor.get("heading")
        stream.seek(offset)
        limit = ctx.cfg.parsing.unit_text_bytes
        lines: list[str] = []
        size = 0
        first_line = line_no + 1
        decode_as = "utf-8" if encoding == "utf-8-sig" and offset else encoding
        for raw in stream:
            offset += len(raw)
            line_no += 1
            text = raw.decode(decode_as, errors="replace").rstrip("\r\n").lstrip("﻿")
            match = _MD_HEADING.match(text) if self.markdown else None
            if match:
                if heading is not None or any(s.strip() for s in lines):
                    yield self._unit(
                        heading,
                        lines,
                        first_line,
                        line_no - 1,
                        offset - len(raw),
                        line_no - 1,
                        encoding,
                        None,
                    )
                heading, lines, size, first_line = match.group(2), [], 0, line_no
                continue
            lines.append(text)
            size += len(text) + 1
            if size >= limit:
                yield self._unit(
                    heading, lines, first_line, line_no, offset, line_no, encoding, heading
                )
                lines, size, first_line = [], 0, line_no + 1
        if heading is not None or any(s.strip() for s in lines):
            yield self._unit(heading, lines, first_line, line_no, offset, line_no, encoding, None)
        yield Unit("note", {}, "", {"done": True}, notes, stats={"lines": line_no})

    @staticmethod
    def _unit(
        heading: str | None,
        lines: list[str],
        first: int,
        last: int,
        offset: int,
        line_no: int,
        encoding: str,
        carry: str | None,
    ) -> Unit:
        text = "\n".join(lines)
        return Unit(
            "section",
            {"section": heading or "(no heading)", "lines": [first, max(first, last)]},
            text,
            {"offset": offset, "line": line_no, "encoding": encoding, "heading": carry},
            heading=heading,
            stats={"words": len(text.split()), "empty": not text.strip()},
        )


_BLOCKS = frozenset({"p", "li", "td", "th", "pre", "blockquote", "dd", "dt", "caption"})
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "title"})


class HtmlParser:
    """HTML streamed with a recovering parser; scripts and styles are ignored."""

    def units(self, stream: IO[bytes], cursor: dict[str, Any], ctx: ParseContext) -> Iterator[Unit]:
        """See the module docstring."""
        if cursor.get("done"):
            return
        from lxml import etree

        stream.seek(0)
        skip = int(cursor.get("element", 0))
        heading: str | None = cursor.get("heading")
        limit = ctx.cfg.parsing.unit_text_bytes
        lines: list[str] = []
        size = 0
        index = 0
        first = skip + 1
        parser = etree.iterparse(stream, events=("end",), html=True, recover=True, huge_tree=True)
        try:
            for _, element in parser:
                tag = element.tag.lower() if isinstance(element.tag, str) else ""
                if tag in ("script", "style", "noscript"):
                    element.clear()
                    continue
                if tag not in _BLOCKS and tag not in _HEADINGS:
                    continue
                index += 1
                if index <= skip:
                    element.clear()
                    continue
                text = " ".join("".join(element.itertext()).split())
                element.clear()
                if tag in _HEADINGS:
                    if heading is not None or lines:
                        yield self._unit(heading, lines, first, index - 1, None)
                    heading, lines, size, first = text or tag, [], 0, index
                    continue
                if text:
                    lines.append(text)
                    size += len(text)
                if size >= limit:
                    yield self._unit(heading, lines, first, index, heading)
                    lines, size, first = [], 0, index + 1
        except etree.XMLSyntaxError as error:
            yield fatal(
                "note",
                {"element": index},
                "truncated_or_corrupt",
                f"The HTML cannot be read past element {index} ({str(error)[:120]}).",
                "Save the page again and upload it.",
            )
            return
        if heading is not None or lines:
            yield self._unit(heading, lines, first, index, None)
        stream.seek(0)
        head = stream.read(4096).lower()
        stream.seek(-min(4096, _size(stream)), 2)
        tail = stream.read().lower()
        notes = []
        if (
            (b"<html" in head or b"<body" in head)
            and b"</html>" not in tail
            and b"</body>" not in tail
        ):
            notes.append(
                Finding(
                    "warn",
                    "truncated_or_corrupt",
                    {},
                    "The page has no closing </body> or </html> tag; it may be truncated.",
                    "Save the complete page again and upload it.",
                )
            )
        yield Unit("note", {}, "", {"done": True}, notes)

    @staticmethod
    def _unit(
        heading: str | None, lines: list[str], first: int, last: int, carry: str | None
    ) -> Unit:
        text = "\n".join(lines)
        return Unit(
            "section",
            {"section": heading or "(no heading)", "elements": [first, max(first, last)]},
            text,
            {"element": last, "heading": carry},
            heading=heading,
            stats={"words": len(text.split()), "empty": not text.strip()},
        )
