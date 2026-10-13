"""The malware scan every upload passes before anything parses it (fail closed).

* ``SignatureScanner`` (local profiles): the EICAR test signature anywhere in the file;
  executables (PE, ELF, Mach-O headers); a PDF with an action that runs code (``/JavaScript``,
  ``/JS``, ``/Launch``, ``/EmbeddedFile``); an Office file carrying macros (``vbaProject.bin``,
  macro-enabled content types); an archive member that is an executable or script. It reads the
  stream in bounded blocks with an overlap, so a signature split across blocks is found.
* ``ClamdScanner``: the clamd ``INSTREAM`` protocol over TCP. Any error, timeout or unexpected
  reply is ``unavailable``, never ``clean``.
* ``UnavailableScanner``: every verdict is ``unavailable`` (the cloud default until clamd is
  configured): the document is rejected ``scan_unavailable`` and never parsed.

GUARANTEE(test_infected_upload_is_rejected_before_parsing)
GUARANTEE(test_an_unreachable_scanner_fails_closed)
"""

from __future__ import annotations

import asyncio
import io
import re
import struct
import zipfile
from dataclasses import dataclass
from typing import IO, Literal, Protocol

EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
_BLOCK = 1 << 20
_OVERLAP = 128
_PDF_ACTIONS = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFile|RichMedia)\b")
_EXEC_HEADERS = (b"MZ", b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe")
_SCRIPT_SUFFIXES = (
    ".exe", ".dll", ".scr", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".hta", ".msi", ".jar", ".sh", ".lnk",
)  # fmt: skip
_MACRO_TYPES = (b"macroEnabled", b"vbaProject")


@dataclass(frozen=True)
class ScanVerdict:
    """``clean``, ``infected`` (with the signature name) or ``unavailable`` (fail closed)."""

    result: Literal["clean", "infected", "unavailable"]
    signature: str | None = None
    engine: str = ""


class ScanPort(Protocol):
    """Scan one stream; never raises for a file's content (errors are ``unavailable``)."""

    async def scan(self, stream: IO[bytes]) -> ScanVerdict:
        """The verdict for the whole stream (read from its current position)."""
        ...


class SignatureScanner:
    """See the module docstring. Local profiles only: it is not a virus scanner."""

    engine = "signature-v1"

    async def scan(self, stream: IO[bytes]) -> ScanVerdict:
        """Signature and structure checks over the whole stream."""
        return await asyncio.to_thread(self._scan, stream)

    def _scan(self, stream: IO[bytes]) -> ScanVerdict:
        stream.seek(0)
        head = stream.read(8)
        if head.startswith(_EXEC_HEADERS):
            return ScanVerdict("infected", "executable", self.engine)
        stream.seek(0)
        is_pdf = head.startswith(b"%PDF-")
        tail = b""
        # LOOP(signature scan blocks): cap=file size / 1 MiB blocks; exit=end of stream, a hit
        while True:
            block = stream.read(_BLOCK)
            if not block:
                break
            window = tail + block
            if EICAR in window:
                return ScanVerdict("infected", "eicar-test-signature", self.engine)
            if is_pdf and _PDF_ACTIONS.search(window):
                return ScanVerdict("infected", "pdf-active-content", self.engine)
            tail = window[-_OVERLAP:]
        stream.seek(0)
        if head.startswith(b"PK\x03\x04"):
            return self._zip(stream)
        return ScanVerdict("clean", None, self.engine)

    def _zip(self, stream: IO[bytes]) -> ScanVerdict:
        try:
            with zipfile.ZipFile(stream) as z:
                for info in z.infolist():
                    lower = info.filename.lower()
                    if lower.endswith("vbaproject.bin"):
                        return ScanVerdict("infected", "office-macro", self.engine)
                    if lower.endswith(_SCRIPT_SUFFIXES):
                        return ScanVerdict("infected", "archive-executable", self.engine)
                    if lower == "[content_types].xml" and info.file_size < (4 << 20):
                        types = z.read(info)
                        if any(t in types for t in _MACRO_TYPES):
                            return ScanVerdict("infected", "office-macro", self.engine)
                    if info.file_size < (32 << 20) and not info.is_dir():
                        with z.open(info) as member:
                            body = member.read(32 << 20)
                        if EICAR in body or body.startswith(_EXEC_HEADERS):
                            return ScanVerdict("infected", "archive-member", self.engine)
        except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
            # an unreadable container is not proven clean
            return ScanVerdict("infected", "unreadable-archive", self.engine)
        finally:
            stream.seek(0)
        return ScanVerdict("clean", None, self.engine)


@dataclass(frozen=True)
class ClamdScanner:
    """See the module docstring."""

    host: str
    port: int = 3310
    timeout_s: float = 120.0
    engine: str = "clamd"

    async def scan(self, stream: IO[bytes]) -> ScanVerdict:
        """INSTREAM the whole stream; anything but a clear reply is ``unavailable``."""
        try:
            return await asyncio.wait_for(self._scan(stream), self.timeout_s)
        except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError):
            return ScanVerdict("unavailable", None, self.engine)

    async def _scan(self, stream: IO[bytes]) -> ScanVerdict:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            writer.write(b"zINSTREAM\0")
            stream.seek(0)
            # LOOP(clamd stream chunks): cap=file size / 1 MiB chunks; exit=end of stream
            while True:
                block = stream.read(_BLOCK)
                if not block:
                    break
                writer.write(struct.pack(">I", len(block)) + block)
                await writer.drain()
            writer.write(struct.pack(">I", 0))
            await writer.drain()
            reply = (await reader.readuntil(b"\0")).rstrip(b"\0").decode("utf-8", "replace")
        finally:
            writer.close()
            stream.seek(0)
        if reply.endswith("OK"):
            return ScanVerdict("clean", None, self.engine)
        if reply.endswith("FOUND"):
            name = reply.split(":", 1)[-1].removesuffix("FOUND").strip()[:120]
            return ScanVerdict("infected", name or "clamd", self.engine)
        return ScanVerdict("unavailable", None, self.engine)


class UnavailableScanner:
    """See the module docstring."""

    engine = "none"

    async def scan(self, stream: IO[bytes]) -> ScanVerdict:
        """Always ``unavailable``."""
        del stream
        return ScanVerdict("unavailable", None, self.engine)


def scanner_for(app_env: str, kind: str, host: str | None, port: int) -> ScanPort:
    """The configured scanner. Staging and production accept clamd only (fail closed)."""
    if kind == "clamd":
        return ClamdScanner(host, port) if host else UnavailableScanner()
    if app_env in ("dev", "test"):
        return SignatureScanner()
    return UnavailableScanner()


def scan_bytes(data: bytes) -> ScanVerdict:
    """Synchronous signature scan of small bytes (tests and archive members)."""
    return SignatureScanner()._scan(io.BytesIO(data))
