"""Google Drive files for a "From a file" research (connectors_client.drive_files): only types a
research can read are listed, Google Docs, Sheets and Slides are exported to docx, xlsx and pptx,
downloads are capped and land in a spool file, and nothing is sent without the caller's token."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from connectors_client import drive_files as D

FILES = [
    {"id": "pdf-file-0001", "name": "Proposal.pdf", "mimeType": "application/pdf", "size": "11"},
    {"id": "doc-file-0001", "name": "Notes", "mimeType": D.GOOGLE_DOC},
    {"id": "img-file-0001", "name": "logo.png", "mimeType": "image/png", "size": "5"},
    {"id": "dir-file-0001", "name": "Folder", "mimeType": "application/vnd.google-apps.folder"},
]


def _transport(seen: list[httpx.Request], body: bytes = b"%PDF-1.7 x") -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/files"):
            return httpx.Response(200, json={"files": FILES})
        if path.endswith("/export"):
            return httpx.Response(200, content=b"PK\x03\x04 docx bytes")
        if path.endswith("/files/missing-file-01"):
            return httpx.Response(404, json={})
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=body)
        found = next(f for f in FILES if path.endswith(f["id"]))
        return httpx.Response(200, json=found)

    return httpx.MockTransport(handle)


def test_list_keeps_only_readable_types_and_searches_by_name() -> None:
    seen: list[httpx.Request] = []
    files = asyncio.run(D.HttpDriveFiles(_transport(seen)).list("tok", "Prop'osal"))
    assert [f.id for f in files] == ["pdf-file-0001", "doc-file-0001"]
    assert files[1].import_name == "Notes.docx" and files[1].export_mime == D.DOCX
    assert files[0].import_name == "Proposal.pdf" and files[0].size == 11
    q = seen[0].url.params["q"]
    assert "trashed = false" in q and "name contains 'Proposal'" in q  # the quote is stripped
    assert seen[0].headers["Authorization"] == "Bearer tok"


def test_google_files_are_exported_and_others_downloaded_into_a_spool() -> None:
    seen: list[httpx.Request] = []
    reader = D.HttpDriveFiles(_transport(seen))
    doc = asyncio.run(reader.get("tok", "doc-file-0001"))
    spool = asyncio.run(reader.download("tok", doc, max_bytes=1000))
    try:
        assert spool.read_bytes().startswith(b"PK") and spool.suffix == ".docx"
        assert seen[-1].url.params["mimeType"] == D.DOCX
    finally:
        spool.unlink()
    pdf = asyncio.run(reader.get("tok", "pdf-file-0001"))
    spool = asyncio.run(reader.download("tok", pdf, max_bytes=1000))
    try:
        assert spool.read_bytes() == b"%PDF-1.7 x"
        assert seen[-1].url.params["alt"] == "media"
    finally:
        spool.unlink()


def test_downloads_are_capped_and_bad_ids_or_types_are_refused() -> None:
    seen: list[httpx.Request] = []
    reader = D.HttpDriveFiles(_transport(seen, body=b"x" * 50))
    pdf = D.DriveFile("pdf-file-0002", "big.pdf", "application/pdf", None, None)
    with pytest.raises(D.DriveUnavailable) as refused:
        asyncio.run(reader.download("tok", pdf, max_bytes=10))
    assert refused.value.code == "too_large"
    declared = D.DriveFile("pdf-file-0003", "big.pdf", "application/pdf", 99, None)
    with pytest.raises(D.DriveUnavailable):
        asyncio.run(reader.download("tok", declared, max_bytes=10))
    for bad in ("../etc/passwd", "short"):
        with pytest.raises(D.DriveUnavailable) as refused:
            asyncio.run(reader.get("tok", bad))
        assert refused.value.code == "drive_file_not_found"
    with pytest.raises(D.DriveUnavailable) as refused:
        asyncio.run(reader.get("tok", "missing-file-01"))
    assert refused.value.code == "drive_file_not_found"
    with pytest.raises(D.DriveUnavailable) as refused:
        asyncio.run(reader.get("tok", "img-file-0001"))
    assert refused.value.code == "wrong_type"


def test_no_token_without_a_registered_app() -> None:
    def txn(_: Any) -> Any:
        raise AssertionError("no database access before the app check")

    with pytest.raises(D.DriveUnavailable) as refused:
        asyncio.run(D.user_drive_token(txn, None, "u", oauth=None, store=None, apps={}))  # type: ignore[arg-type]
    assert refused.value.code == "not_available"
