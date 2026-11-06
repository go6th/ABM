"""The malware scan every upload passes before anything parses it (fail closed).

* ``ClamdScanner``: the clamd ``INSTREAM`` protocol over TCP. Any error, timeout or unexpected
  reply is ``unavailable``, never ``clean``.
* ``UnavailableScanner``: every verdict is ``unavailable`` (the default in EVERY environment until
  clamd is configured): the document is never parsed and never marked clean. There is no
  signature-check stand-in: local compose runs the same ClamAV container as staging, so both
  accept the same files (a test double lives in ``tests/fakes/scanner.py``).

GUARANTEE(test_infected_upload_is_rejected_before_parsing)
GUARANTEE(test_an_unreachable_scanner_fails_closed)
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import IO, Literal, Protocol

EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
_BLOCK = 1 << 20


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


_EXEC_HEADERS = (b"MZ", b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe")
_MACRO_TYPES = (b"macroEnabled", b"vbaProject")
_MEMBER_HEAD = 8


@dataclass(frozen=True)
class StructureGuard:
    """What stock ClamAV lets through and no upload of this product needs, refused by structure
    in front of the scanner in every environment (the same files are accepted locally and in the
    cloud): an executable (PE, ELF and Mach-O headers), an Office file carrying macros
    (``vbaProject.bin`` or a macro-enabled content type), and either one inside a zip. Names are
    never judged: a ``.py`` or ``.js`` file is text and goes to the scanner like any other. Only
    a structure the guard passes reaches ``inner``, whose verdict stands (an unavailable scanner
    stays unavailable)."""

    inner: ScanPort
    engine: str = "structure-guard"

    async def scan(self, stream: IO[bytes]) -> ScanVerdict:
        """The guard's refusal, else the inner scanner's verdict."""
        found = await asyncio.to_thread(self._structure, stream)
        stream.seek(0)
        if found is not None:
            return ScanVerdict("infected", found, self.engine)
        return await self.inner.scan(stream)

    def _structure(self, stream: IO[bytes]) -> str | None:
        import zipfile

        stream.seek(0)
        if stream.read(_MEMBER_HEAD).startswith(_EXEC_HEADERS):
            return "executable"
        stream.seek(0)
        try:
            archive = zipfile.ZipFile(stream)
        except (zipfile.BadZipFile, OSError, ValueError):
            return None  # not a zip: the scanner and the type check judge it
        with archive:
            # LOOP(structure guard members): cap=the archive's members; exit=last member, a hit
            for info in archive.infolist():
                lower = info.filename.lower()
                if lower.endswith("vbaproject.bin"):
                    return "office-macro"
                if info.is_dir():
                    continue
                try:
                    with archive.open(info) as member:
                        head = member.read(_MEMBER_HEAD)
                        if lower == "[content_types].xml" and info.file_size < (4 << 20):
                            head += member.read()
                            if any(t in head for t in _MACRO_TYPES):
                                return "office-macro"
                except (zipfile.BadZipFile, OSError, RuntimeError, ValueError, EOFError):
                    continue  # an unreadable member is the scanner's and the parser's to judge
                if head.startswith(_EXEC_HEADERS):
                    return "archive-executable"
        return None


def scanner_for(app_env: str, kind: str, host: str | None, port: int) -> ScanPort:
    """The configured scanner behind the structure guard: clamd with a host, otherwise
    unavailable in every environment."""
    del app_env  # dev and test fail closed exactly like staging and production
    if kind == "clamd" and host:
        return StructureGuard(ClamdScanner(host, port))
    return StructureGuard(UnavailableScanner())
