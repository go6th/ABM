"""Files from the signed-in user's own Google Drive for a "From a file" research (owner decision
2026-10-15): list the files a research can read, and download one on the server into the same
quarantine, scan and reading path as an upload.

* ``user_drive_token``: the caller's active Google Drive connection (Mail and drive page) and a
  fresh access token; a revoked or changed grant marks the connection broken (fail closed). The
  token is used on the server only: it never reaches the browser and is never logged.
* ``HttpDriveFiles``: ``list`` (name search, newest first, only types a research can read, never
  folders or trash), ``get`` (one file's metadata) and ``download`` (bytes, or for a Google Doc,
  Sheet or Slides file its export as docx, xlsx or pptx), capped at ``max_bytes`` and written to
  a local spool file that the caller streams into quarantine and deletes.
* ``IMPORTABLE``: Drive type to the extension the document service declares. The file is still
  typed from its content and scanned before anything reads it.

[VERIFY] The Drive v3 paths and fields follow Google's public documentation; exercised here
against fakes until the owner registers the Mail and drive OAuth app.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from account_intelligence.config import get_settings

GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

# Drive mime type -> (extension, export mime type for Google's own formats)
IMPORTABLE: dict[str, tuple[str, str | None]] = {
    "application/pdf": ("pdf", None),
    DOCX: ("docx", None),
    XLSX: ("xlsx", None),
    PPTX: ("pptx", None),
    "text/csv": ("csv", None),
    "text/plain": ("txt", None),
    "text/markdown": ("md", None),
    GOOGLE_DOC: ("docx", DOCX),
    GOOGLE_SHEET: ("xlsx", XLSX),
    GOOGLE_SLIDES: ("pptx", PPTX),
}
PAGE_SIZE = 50
FIELDS = "id,name,mimeType,size,modifiedTime"
_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_QUOTE = re.compile(r"['\\]")


class DriveUnavailable(Exception):
    """Why no Drive file can be listed or imported; ``code`` is the API's reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DriveFile:
    """One file a research can read, as Drive lists it (metadata only)."""

    id: str
    name: str
    mime: str
    size: int | None  # None for Google Docs, Sheets and Slides (exported on import)
    modified: str | None

    @property
    def extension(self) -> str:
        """The file extension the stored copy gets."""
        return IMPORTABLE[self.mime][0]

    @property
    def export_mime(self) -> str | None:
        """The export format a Google Docs file is fetched as, or None."""
        return IMPORTABLE[self.mime][1]

    @property
    def import_name(self) -> str:
        """The name the document service sees: the file's name with the extension it reads."""
        base = self.name.strip() or "Untitled"
        suffix = "." + self.extension
        return base if base.lower().endswith(suffix) else base + suffix


def valid_file_id(file_id: str) -> bool:
    """A Drive file id as Google issues them (letters, digits, - and _), nothing else."""
    return bool(_FILE_ID.match(file_id))


def _file(raw: dict[str, Any]) -> DriveFile | None:
    mime = str(raw.get("mimeType") or "")
    if mime not in IMPORTABLE or not raw.get("id"):
        return None
    size = raw.get("size")
    return DriveFile(
        id=str(raw["id"]),
        name=str(raw.get("name") or "Untitled"),
        mime=mime,
        size=int(size) if size not in (None, "") else None,
        modified=str(raw["modifiedTime"]) if raw.get("modifiedTime") else None,
    )


class HttpDriveFiles:
    """Google Drive v3 reads for one access token (see the module docstring)."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    def _client(self, access_token: str, timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self.transport,
            timeout=timeout,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    @staticmethod
    def _base() -> str:
        return get_settings().google_drive_api_base.rstrip("/")

    async def list(self, access_token: str, query: str = "") -> list[DriveFile]:
        """At most ``PAGE_SIZE`` readable files, newest first, optionally whose name contains
        ``query``."""
        types = " or ".join(f"mimeType = '{m}'" for m in IMPORTABLE)
        q = f"trashed = false and ({types})"
        words = _QUOTE.sub("", query.strip())[:100]
        if words:
            q += f" and name contains '{words}'"
        async with self._client(access_token) as client:
            reply = await client.get(
                f"{self._base()}/files",
                params={
                    "q": q,
                    "orderBy": "modifiedTime desc",
                    "pageSize": str(PAGE_SIZE),
                    "fields": f"files({FIELDS})",
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                },
            )
        reply.raise_for_status()
        listed = (reply.json().get("files") or [])[:PAGE_SIZE]
        return [f for f in (_file(raw) for raw in listed) if f is not None]

    async def get(self, access_token: str, file_id: str) -> DriveFile:
        """One file's metadata; ``DriveUnavailable`` when it is missing or not readable."""
        if not valid_file_id(file_id):
            raise DriveUnavailable("drive_file_not_found", "That Drive file was not found.")
        async with self._client(access_token) as client:
            reply = await client.get(
                f"{self._base()}/files/{file_id}",
                params={"fields": FIELDS, "supportsAllDrives": "true"},
            )
        if reply.status_code in (403, 404):
            raise DriveUnavailable("drive_file_not_found", "That Drive file was not found.")
        reply.raise_for_status()
        found = _file(reply.json())
        if found is None:
            raise DriveUnavailable(
                "wrong_type",
                "That file type cannot be read. Use PDF, Word, Excel, PowerPoint, CSV or text.",
            )
        return found

    async def download(self, access_token: str, file: DriveFile, max_bytes: int) -> Path:
        """The file's bytes (or its export) in a new spool file, at most ``max_bytes``; the caller
        deletes it. ``DriveUnavailable("too_large")`` past the limit."""
        if file.size is not None and file.size > max_bytes:
            raise DriveUnavailable("too_large", "The file is larger than your workspace allows.")
        if file.export_mime:
            url = f"{self._base()}/files/{file.id}/export"
            params = {"mimeType": file.export_mime}
        else:
            url = f"{self._base()}/files/{file.id}"
            params = {"alt": "media", "supportsAllDrives": "true"}
        handle, name = tempfile.mkstemp(prefix="drive-", suffix="." + file.extension)
        os.close(handle)
        spool = Path(name)
        written = 0
        try:
            async with (
                self._client(access_token, timeout=300.0) as client,
                client.stream("GET", url, params=params) as reply,
            ):
                if reply.status_code in (403, 404):
                    raise DriveUnavailable(
                        "drive_file_not_found", "That Drive file could not be downloaded."
                    )
                reply.raise_for_status()
                with spool.open("wb") as out:
                    # LOOP(drive download): cap=max_bytes; exit=end of body or over the cap
                    async for chunk in reply.aiter_bytes(1 << 20):
                        written += len(chunk)
                        if written > max_bytes:
                            raise DriveUnavailable(
                                "too_large", "The file is larger than your workspace allows."
                            )
                        out.write(chunk)
        except BaseException:  # a cancelled or failed download leaves no partial spool file
            spool.unlink(missing_ok=True)
            raise
        if written == 0:
            spool.unlink(missing_ok=True)
            raise DriveUnavailable("empty", "The file is empty.")
        return spool


async def user_drive_token(
    txn: Any, tenant_id: UUID, user: str, *, oauth: Any, store: Any, apps: dict[str, Any]
) -> str:
    """A fresh access token for the caller's own active Google Drive connection (see the module
    docstring); ``DriveUnavailable`` with the reason otherwise."""
    from sqlalchemy import text

    from account_intelligence.replica.settings import client_settings
    from connectors_client import oauth as consent
    from connectors_client.scopes import load, spec

    if spec("google_drive").provider not in apps:
        raise DriveUnavailable(
            "not_available", "Google Drive is not set up for this workspace yet."
        )
    async with txn(tenant_id) as s:
        if not (await client_settings(s)).mail_drive_allowed:
            raise DriveUnavailable(
                "not_allowed", "Your workspace does not allow Drive connections."
            )
        conn = (
            await s.execute(
                text(
                    "SELECT id, token_ref, scopes FROM connector_connections"
                    " WHERE owner_user_id = :u AND kind = 'google_drive' AND status = 'active'"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"u": user},
            )
        ).first()
        if conn is None:
            raise DriveUnavailable(
                "drive_not_connected", "Connect Google Drive on the Mail and drive page first."
            )
        if not conn.token_ref:
            await consent.mark_broken(s, conn.id, "token_missing")
            raise DriveUnavailable(
                "drive_broken", "Reconnect Google Drive on the Mail and drive page."
            )
    try:
        tokens = await consent.access_token(
            oauth,
            store,
            kind="google_drive",
            token_ref=conn.token_ref,
            granted=tuple(conn.scopes or ()),
            attempts=load()[1].refresh_attempts,
        )
    except consent.ConnectorRefused as refused:
        async with txn(tenant_id) as s:
            await consent.mark_broken(s, conn.id, refused.reason)
        raise DriveUnavailable(
            "drive_broken", "Reconnect Google Drive on the Mail and drive page."
        ) from None
    return str(tokens.access_token)
