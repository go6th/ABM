"""An archive build leaves the worker's event loop free (staging, 2026-10-20): compressing and
copying into a zip part run in a worker thread, so the liveness probe and a research slice on the
same instance keep being served while a large archive is written."""

from __future__ import annotations

import asyncio
import io
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from account_intelligence.replica import downloads as D

pytestmark = pytest.mark.asyncio


class _SlowReader(io.RawIOBase):
    """A file whose every block takes a blocking moment to read (a slow mount)."""

    def __init__(self, blocks: int) -> None:
        self.left = blocks

    def read(self, size: int = -1) -> bytes:
        if self.left == 0:
            return b""
        self.left -= 1
        time.sleep(0.05)  # blocking, as a mounted bucket or a deflate pass is
        return b"x" * 1024


async def test_writing_a_part_does_not_block_the_event_loop(tmp_path: Path) -> None:
    src = D.Sources(txn=None, payloads=None, blobs=None)  # type: ignore[arg-type]
    writer = D._Parts(uuid4(), uuid4(), "Acme", tmp_path, src)
    ticks = 0

    async def probe() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(probe())
    try:
        await writer.add_stream("Internal - your files/big.bin", _SlowReader(10))  # ~0.5 s
        await writer.add_bytes("README.txt", b"hello")
    finally:
        beat.cancel()
    assert ticks >= 10  # the loop kept serving while the part was written
    assert writer.files == 2
    await asyncio.to_thread(writer._seal)
    (_, path), *_ = writer.pending
    assert zipfile.ZipFile(path).namelist() == ["Internal - your files/big.bin", "README.txt"]
