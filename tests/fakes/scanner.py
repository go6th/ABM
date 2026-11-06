"""A stand-in for clamd in hermetic tests: it answers the way the real scanner does.

The EICAR antivirus test string is infected, everything else is clean. It looks at content only,
never at file names or extensions, so ``.py`` and ``.js`` files pass exactly as they do in
ClamAV. Production code has no such scanner: with no clamd configured every upload is
``unavailable`` (fail closed) in every environment. Tests that need a working scanner get this
one through the ``fake_clamd`` autouse fixture in ``tests/conftest.py``.
"""

from __future__ import annotations

import asyncio
import io
from typing import IO

from documents.scan import EICAR, ScanVerdict

_BLOCK = 1 << 20
_OVERLAP = 128


class FakeClamd:
    """Content-only scanner double: EICAR is infected, the rest is clean."""

    engine = "fake-clamd"

    async def scan(self, stream: IO[bytes]) -> ScanVerdict:
        """Scan the whole stream from its start."""
        return await asyncio.to_thread(self._scan, stream)

    def _scan(self, stream: IO[bytes]) -> ScanVerdict:
        stream.seek(0)
        tail = b""
        while True:
            block = stream.read(_BLOCK)
            if not block:
                break
            if EICAR in tail + block:
                stream.seek(0)
                return ScanVerdict("infected", "Win.Test.EICAR_HDB-1", self.engine)
            tail = (tail + block)[-_OVERLAP:]
        stream.seek(0)
        return ScanVerdict("clean", None, self.engine)


class DownScanner:
    """A scanner that cannot be reached (fail-closed tests)."""

    engine = "down"

    async def scan(self, stream: IO[bytes]) -> ScanVerdict:
        """Always unavailable."""
        del stream
        return ScanVerdict("unavailable", None, self.engine)


def scan_bytes(data: bytes) -> ScanVerdict:
    """Synchronous verdict for small bytes."""
    return FakeClamd()._scan(io.BytesIO(data))
