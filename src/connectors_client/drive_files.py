"""Files from the signed-in user's own Google Drive or OneDrive for a "From a file" research
(owner decision 2026-10-15): list the files a research can read, and download one on the server
into the same quarantine, scan and reading path as an upload.

* ``user_drive_token``: the caller's active Google Drive or OneDrive connection (Mail and drive
  page, ``kind`` = the source) and a fresh access token; a revoked or changed grant marks the
  connection broken (fail closed). The token is used on the server only: it never reaches the
  browser and is never logged.
* ``HttpDriveFiles`` (Google Drive v3): ``list`` (name search, newest first, only types a
  research can read, never folders or trash), ``get`` (one file's metadata) and ``download``
  (bytes, or for a Google Doc, Sheet or Slides file its export as docx, xlsx or pptx), capped at
  ``max_bytes`` and written to a local spool file that the caller streams into quarantine and
  deletes.
* ``HttpOneDriveFiles`` (Microsoft Graph, scope Files.Read): the same three reads. ``list`` is
  ``/me/drive/recent`` or, with a query, ``/me/drive/root/search(q=...)``, following
  ``@odata.nextLink`` (on the Graph host only) up to ``PAGE_SIZE`` files; folders, packages,
  deleted items and items shared from another drive are skipped, and a file is readable by its
  extension. ``download`` asks ``/me/drive/items/{id}/content`` and follows Graph's redirect to
  the pre-authenticated download URL with a client that carries no Authorization header, so the
  token never reaches the download host. 429 and 503 are retried after Retry-After (bounded).
* ``IMPORTABLE``: Drive type to the extension the document service declares. The file is still
  typed from its content and scanned before anything reads it.

[VERIFY] The Drive v3 and Graph paths and fields follow Google's and Microsoft's public
documentation; exercised here against fakes until the owner registers the Mail and drive OAuth
apps.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit
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
# OneDrive reports no Google formats: a file is readable by its extension (then typed from its
# content and scanned like any upload)
BY_EXTENSION: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": DOCX,
    "xlsx": XLSX,
    "pptx": PPTX,
    "csv": "text/csv",
    "txt": "text/plain",
    "md": "text/markdown",
}
SOURCES = ("google_drive", "onedrive")
LABEL = {"google_drive": "Google Drive", "onedrive": "OneDrive"}
PAGE_SIZE = 50
FIELDS = "id,name,mimeType,size,modifiedTime"
ONEDRIVE_FIELDS = "id,name,size,file,folder,package,remoteItem,deleted,lastModifiedDateTime"
ONEDRIVE_MAX_PAGES = 10
RETRY_ATTEMPTS = 3
RETRY_WAIT_CAP_S = 10.0
_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
# OneDrive for Business ids are letters and digits; personal ones read "<hex>!<number>"
_ONEDRIVE_ID = re.compile(r"^[A-Za-z0-9!_-]{10,200}$")
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


def valid_onedrive_id(file_id: str) -> bool:
    """A OneDrive item id as Graph issues them (letters, digits, !, - and _), nothing else."""
    return bool(_ONEDRIVE_ID.match(file_id))


def _onedrive_file(raw: dict[str, Any]) -> DriveFile | None:
    """A readable file as Graph lists it, or None (folder, package, deleted, shared from another
    drive, unknown extension or malformed id)."""
    if any(raw.get(k) for k in ("folder", "package", "remoteItem", "deleted")):
        return None
    if not isinstance(raw.get("file"), dict):
        return None
    file_id = str(raw.get("id") or "")
    name = str(raw.get("name") or "")
    mime = BY_EXTENSION.get(name.rsplit(".", 1)[-1].lower()) if "." in name else None
    if mime is None or not valid_onedrive_id(file_id):
        return None
    size = raw.get("size")
    return DriveFile(
        id=file_id,
        name=name,
        mime=mime,
        size=int(size) if isinstance(size, int | str) and str(size).isdigit() else None,
        modified=str(raw["lastModifiedDateTime"]) if raw.get("lastModifiedDateTime") else None,
    )


def _retry_after(value: str | None) -> float:
    """Seconds to wait before retrying a throttled Graph call: Retry-After when it is a number,
    capped at ``RETRY_WAIT_CAP_S``; 1 second otherwise."""
    try:
        seconds = float(value) if value is not None else 1.0
    except ValueError:
        seconds = 1.0
    return min(max(seconds, 0.0), RETRY_WAIT_CAP_S)


class HttpOneDriveFiles:
    """Microsoft Graph OneDrive reads for one access token (see the module docstring)."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        base: str | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.transport = transport
        self.base = base
        self.sleep = sleep

    def _base(self) -> str:
        return (self.base or get_settings().microsoft_graph_api_base).rstrip("/")

    def _client(self, access_token: str, timeout: float = 30.0) -> httpx.AsyncClient:
        # never follows a redirect: the bearer token stays on the Graph host
        return httpx.AsyncClient(
            transport=self.transport,
            timeout=timeout,
            follow_redirects=False,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    def _on_graph(self, url: str) -> bool:
        base, there = urlsplit(self._base()), urlsplit(url)
        return (there.scheme, there.netloc) == (base.scheme, base.netloc)

    async def _send(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict[str, str] | None = None,
        *,
        stream: bool = False,
    ) -> httpx.Response:
        """GET ``url``, retrying 429 and 503 after Retry-After at most ``RETRY_ATTEMPTS`` times."""
        # LOOP(graph throttling): cap=RETRY_ATTEMPTS attempts, each wait at most RETRY_WAIT_CAP_S;
        # exit=an answer other than 429 or 503, or the attempts are spent (that answer is returned)
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            reply = await client.send(
                client.build_request("GET", url, params=params), stream=stream
            )
            if reply.status_code not in (429, 503) or attempt == RETRY_ATTEMPTS:
                return reply
            wait = _retry_after(reply.headers.get("Retry-After"))
            await reply.aclose()
            await self.sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    async def list(self, access_token: str, query: str = "") -> list[DriveFile]:
        """At most ``PAGE_SIZE`` readable files, newest first: the recent files, or those whose
        name or content matches ``query``."""
        words = _QUOTE.sub("", query.strip())[:100]
        if words:
            url = f"{self._base()}/me/drive/root/search(q='{quote(words, safe='')}')"
            params: dict[str, str] | None = {"$top": str(PAGE_SIZE), "$select": ONEDRIVE_FIELDS}
        else:
            url, params = f"{self._base()}/me/drive/recent", {"$top": str(PAGE_SIZE)}
        found: list[DriveFile] = []
        async with self._client(access_token) as client:
            # LOOP(onedrive list pages): cap=ONEDRIVE_MAX_PAGES pages; exit=PAGE_SIZE files, no
            # next link, or a next link that is not on the Graph host
            for _ in range(ONEDRIVE_MAX_PAGES):
                reply = await self._send(client, url, params)
                reply.raise_for_status()
                body = reply.json()
                for raw in body.get("value") or []:
                    item = _onedrive_file(raw) if isinstance(raw, dict) else None
                    if item is not None and len(found) < PAGE_SIZE:
                        found.append(item)
                link = body.get("@odata.nextLink")
                if len(found) >= PAGE_SIZE or not isinstance(link, str) or not self._on_graph(link):
                    break
                url, params = link, None  # the next link carries its own query
        return sorted(found, key=lambda f: f.modified or "", reverse=True)

    async def get(self, access_token: str, file_id: str) -> DriveFile:
        """One file's metadata; ``DriveUnavailable`` when it is missing or not readable."""
        if not valid_onedrive_id(file_id):
            raise DriveUnavailable("drive_file_not_found", "That OneDrive file was not found.")
        async with self._client(access_token) as client:
            reply = await self._send(
                client, f"{self._base()}/me/drive/items/{file_id}", {"$select": ONEDRIVE_FIELDS}
            )
        if reply.status_code in (403, 404):
            raise DriveUnavailable("drive_file_not_found", "That OneDrive file was not found.")
        reply.raise_for_status()
        found = _onedrive_file(reply.json())
        if found is None or found.id != file_id:
            raise DriveUnavailable(
                "wrong_type",
                "That file type cannot be read. Use PDF, Word, Excel, PowerPoint, CSV or text.",
            )
        return found

    async def download(self, access_token: str, file: DriveFile, max_bytes: int) -> Path:
        """The file's bytes in a new spool file, at most ``max_bytes``; the caller deletes it.
        Graph answers with a redirect to a pre-authenticated URL, fetched without the token."""
        if file.size is not None and file.size > max_bytes:
            raise DriveUnavailable("too_large", "The file is larger than your workspace allows.")
        if not valid_onedrive_id(file.id):
            raise DriveUnavailable("drive_file_not_found", "That OneDrive file was not found.")
        handle, name = tempfile.mkstemp(prefix="onedrive-", suffix="." + file.extension)
        os.close(handle)
        spool = Path(name)
        try:
            async with self._client(access_token, timeout=300.0) as graph:
                reply = await self._send(
                    graph, f"{self._base()}/me/drive/items/{file.id}/content", stream=True
                )
                try:
                    if reply.status_code in (403, 404):
                        raise DriveUnavailable(
                            "drive_file_not_found", "That OneDrive file could not be downloaded."
                        )
                    if not reply.is_redirect:
                        reply.raise_for_status()
                        written = await _spool(reply, spool, max_bytes)
                    else:
                        target = urljoin(str(reply.url), reply.headers.get("Location", ""))
                finally:
                    await reply.aclose()
            if reply.is_redirect:
                written = await self._fetch_unauthenticated(target, spool, max_bytes)
        except BaseException:  # a cancelled or failed download leaves no partial spool file
            spool.unlink(missing_ok=True)
            raise
        if written == 0:
            spool.unlink(missing_ok=True)
            raise DriveUnavailable("empty", "The file is empty.")
        return spool

    async def _fetch_unauthenticated(self, url: str, spool: Path, max_bytes: int) -> int:
        """The pre-authenticated download URL: https only, no Authorization header, at most 3
        further redirects."""
        if urlsplit(url).scheme != "https":
            raise httpx.UnsupportedProtocol("the download URL is not https")
        async with httpx.AsyncClient(
            transport=self.transport, timeout=300.0, follow_redirects=True, max_redirects=3
        ) as plain:
            reply = await self._send(plain, url, stream=True)
            try:
                if reply.status_code in (403, 404):
                    raise DriveUnavailable(
                        "drive_file_not_found", "That OneDrive file could not be downloaded."
                    )
                reply.raise_for_status()
                return await _spool(reply, spool, max_bytes)
            finally:
                await reply.aclose()


async def _spool(reply: httpx.Response, spool: Path, max_bytes: int) -> int:
    """Stream ``reply``'s body into ``spool``; ``DriveUnavailable("too_large")`` past the cap."""
    written = 0
    with spool.open("wb") as out:
        # LOOP(onedrive download): cap=max_bytes; exit=end of body or over the cap
        async for chunk in reply.aiter_bytes(1 << 20):
            written += len(chunk)
            if written > max_bytes:
                raise DriveUnavailable(
                    "too_large", "The file is larger than your workspace allows."
                )
            out.write(chunk)
    return written


async def user_drive_token(
    txn: Any,
    tenant_id: UUID,
    user: str,
    *,
    oauth: Any,
    store: Any,
    apps: dict[str, Any],
    kind: str = "google_drive",
) -> str:
    """A fresh access token for the caller's own active connection of ``kind`` (Google Drive or
    OneDrive; see the module docstring); ``DriveUnavailable`` with the reason otherwise. Only a
    connection the caller owns is ever read."""
    from sqlalchemy import text

    from account_intelligence.replica.settings import client_settings
    from connectors_client import oauth as consent
    from connectors_client.scopes import load, spec

    if kind not in SOURCES:
        raise DriveUnavailable("not_available", "That drive is not supported.")
    label = LABEL[kind]
    if spec(kind).provider not in apps:
        raise DriveUnavailable("not_available", f"{label} is not set up for this workspace yet.")
    async with txn(tenant_id) as s:
        if not (await client_settings(s)).mail_drive_allowed:
            raise DriveUnavailable(
                "not_allowed", "Your workspace does not allow Drive connections."
            )
        conn = (
            await s.execute(
                text(
                    "SELECT id, token_ref, scopes FROM connector_connections"
                    " WHERE owner_user_id = :u AND kind = :k AND status = 'active'"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"u": user, "k": kind},
            )
        ).first()
        if conn is None:
            raise DriveUnavailable(
                "drive_not_connected", f"Connect {label} on the Mail and drive page first."
            )
        if not conn.token_ref:
            await consent.mark_broken(s, conn.id, "token_missing")
            raise DriveUnavailable("drive_broken", f"Reconnect {label} on the Mail and drive page.")
    connection_id = conn.id

    async def rotated(new_ref: str) -> bool:
        """Point the connection at a rotated refresh token (Microsoft rotates on every refresh;
        a token not written back would be refused later). False: it is no longer active."""
        async with txn(tenant_id) as s:
            kept: bool = await consent.replace_token_ref(s, connection_id, new_ref)
        return kept

    try:
        tokens = await consent.access_token(
            oauth,
            store,
            kind=kind,
            token_ref=conn.token_ref,
            granted=tuple(conn.scopes or ()),
            attempts=load()[1].refresh_attempts,
            secret_name=f"tenants/{tenant_id}/connectors/{connection_id}",
            on_rotated=rotated,
        )
    except consent.ConnectorRefused as refused:
        if getattr(refused, "transient", False):  # the provider was unreachable: grant kept
            raise DriveUnavailable(
                "drive_unreachable", f"{label} did not answer. Try again."
            ) from None
        async with txn(tenant_id) as s:
            await consent.mark_broken(s, conn.id, refused.reason)
        raise DriveUnavailable(
            "drive_broken", f"Reconnect {label} on the Mail and drive page."
        ) from None
    return str(tokens.access_token)
