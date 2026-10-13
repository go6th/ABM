"""File type detection from the first bytes (magic numbers) first, the name second.

Office formats are zip containers: the member list decides docx, xlsx or pptx. A file no rule
recognises is ``unknown`` (stored, then PARTIAL "unsupported"; never rejected).

GUARANTEE(test_detection_prefers_content_over_the_name)
"""

from __future__ import annotations

import io
import zipfile
from typing import IO

TABULAR = frozenset({"csv", "tsv", "xlsx", "parquet", "json", "jsonl"})
DOCUMENT = frozenset({"pdf", "docx", "pptx", "txt", "md", "html"})
IMAGE = frozenset({"png", "jpeg", "gif", "tiff", "webp", "bmp"})
MEDIA = frozenset({"mp3", "wav", "ogg", "flac", "mp4", "mov", "webm", "mkv", "m4a"})
ARCHIVE = frozenset({"zip", "tar", "gz"})

_EXT = {
    "csv": "csv", "tsv": "tsv", "tab": "tsv", "xlsx": "xlsx", "parquet": "parquet",
    "json": "json", "jsonl": "jsonl", "ndjson": "jsonl", "pdf": "pdf", "docx": "docx",
    "pptx": "pptx", "txt": "txt", "text": "txt", "log": "txt", "md": "md", "markdown": "md",
    "html": "html", "htm": "html", "png": "png", "jpg": "jpeg", "jpeg": "jpeg", "gif": "gif",
    "tif": "tiff", "tiff": "tiff", "webp": "webp", "bmp": "bmp", "mp3": "mp3", "wav": "wav",
    "ogg": "ogg", "flac": "flac", "mp4": "mp4", "m4a": "m4a", "mov": "mov", "webm": "webm",
    "mkv": "mkv", "zip": "zip", "tar": "tar", "gz": "gz", "tgz": "gz",
}  # fmt: skip


def family(kind: str) -> str:
    """tabular, document, image, media, archive or unknown."""
    for name, members in (
        ("tabular", TABULAR),
        ("document", DOCUMENT),
        ("image", IMAGE),
        ("media", MEDIA),
        ("archive", ARCHIVE),
    ):
        if kind in members:
            return name
    return "unknown"


def by_name(name: str) -> str | None:
    """The type the extension names, or None."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _EXT.get(ext)


def _zip_kind(head: IO[bytes]) -> str:
    try:
        with zipfile.ZipFile(head) as z:
            names = set(z.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return "zip"
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if any(n.startswith("ppt/slides/") for n in names):
        return "pptx"
    return "zip"


def _looks_text(sample: bytes) -> bool:
    if not sample:
        return True
    if b"\x00" in sample[:4096]:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError as error:
        # a multi-byte character cut at the end of the sample is still text
        return error.start >= len(sample) - 3 or sample.count(b"\n") > 0


def detect(stream: IO[bytes], name: str) -> str:
    """The file's type (see the module docstring). ``stream`` must be seekable; it is rewound."""
    stream.seek(0)
    head = stream.read(8192)
    stream.seek(0)
    named = by_name(name)
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        kind = _zip_kind(stream)
        stream.seek(0)
        return kind
    if head.startswith(b"PAR1"):
        return "parquet"
    if head.startswith(b"\x1f\x8b"):
        return "gz"
    if len(head) > 262 and head[257:262] == b"ustar":
        return "tar"
    for magic, kind in (
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"\xff\xd8\xff", "jpeg"),
        (b"GIF87a", "gif"),
        (b"GIF89a", "gif"),
        (b"II*\x00", "tiff"),
        (b"MM\x00*", "tiff"),
        (b"BM", "bmp"),
        (b"ID3", "mp3"),
        (b"fLaC", "flac"),
        (b"OggS", "ogg"),
        (b"\x1aE\xdf\xa3", "mkv" if named != "webm" else "webm"),
    ):
        if head.startswith(magic):
            return kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[4:8] == b"ftyp":
        return "mov" if head[8:10] == b"qt" else ("m4a" if named == "m4a" else "mp4")
    if _looks_text(head):
        if named in ("csv", "tsv", "json", "jsonl", "md", "html", "txt"):
            return named
        stripped = head.lstrip()
        if stripped[:1] in (b"{", b"["):
            return "jsonl" if stripped[:1] == b"{" and b"}\n{" in head else "json"
        if stripped[:5].lower() in (b"<!doc", b"<html"):
            return "html"
        return "txt"
    return named if named in ("parquet",) else "unknown"


def sniff(head: bytes) -> str:
    """Detection over bytes alone (archive members): same rules, no name."""
    return detect(io.BytesIO(head), "")
