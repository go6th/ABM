"""The file manifest: ``manifest.json`` beside the rendered files.

One entry per file of the output folder (the manifest itself left out): name, size, SHA-256,
MIME type and whether it is a client file (a refused name is not); plus the bundle version and
the recalculated Summary values the workbook check read, keyed per workbook as
``parity.counts.summary_values`` keys them. Written with sorted keys, so one input gives one
manifest.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from parity.gate.files import refused_name

MANIFEST_NAME = "manifest.json"
_MIME = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".zip": "application/zip",
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".json": "application/json",
}


def mime_type(path: Path) -> str:
    """The MIME type by extension."""
    known = _MIME.get(path.suffix.lower())
    if known:
        return known
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def sha256_of(path: Path) -> str:
    """The file's SHA-256 hex digest."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_of(
    out_dir: Path, bundle_version: str, summary: Mapping[str, Mapping[str, Any]] | None = None
) -> dict[str, Any]:
    """The manifest as data."""
    files = []
    for path in sorted(p for p in out_dir.iterdir() if p.is_file() and p.name != MANIFEST_NAME):
        files.append(
            {
                "name": path.name,
                "size": path.stat().st_size,
                "sha256": sha256_of(path),
                "mime": mime_type(path),
                "client": not refused_name(path.name),
            }
        )
    return {
        "bundle_version": bundle_version,
        "files": files,
        "summary": {k: dict(v) for k, v in (summary or {}).items()},
    }


def write_manifest(
    out_dir: Path, bundle_version: str, summary: Mapping[str, Mapping[str, Any]] | None = None
) -> dict[str, Any]:
    """Write ``manifest.json`` into ``out_dir`` and return it."""
    data = manifest_of(out_dir, bundle_version, summary)
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(data, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return data
