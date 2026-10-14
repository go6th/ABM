"""Subprocess driver for the multi-GB document test: sends a file from disk to the development
object store in resumable chunks (never read whole), completes it, reads it in parts and prints
one JSON line: the peak resident memory of this process and of the largest sandbox child, the
parts, passages and timings.

Usage: python -m tests.documents.large_driver <tenant> <file> <blob_root>
"""

from __future__ import annotations

import asyncio
import json
import os
import resource
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

PUBLIC = "http://store.test"


async def send(url: str, path: Path, chunk: int) -> None:
    """The browser uploader's protocol: Content-Range chunks, read from disk one at a time."""
    from devstore.server import build

    app = build(os.environ["DEVSTORE_ROOT"], PUBLIC)
    size = path.stat().st_size
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC) as c:
        with path.open("rb") as handle:
            offset = 0
            # LOOP(large driver chunks): cap=size / chunk; exit=every byte sent
            while offset < size:
                end = min(offset + chunk, size) - 1
                reply = await c.put(
                    url.removeprefix(PUBLIC),
                    content=_body(handle, end + 1 - offset),
                    headers={"Content-Range": f"bytes {offset}-{end}/{size}"},
                )
                assert reply.status_code in (200, 308), reply.text
                offset = end + 1


async def _body(handle: Any, length: int) -> AsyncIterator[bytes]:
    """One chunk's body, streamed from disk in 1 MiB pieces as a browser sends it (a whole 32 MiB
    chunk as one bytes object stays alive in the in-process transport's reference cycles until a
    full collection, so the driver's memory would grow with the file)."""
    # LOOP(large driver body pieces): cap=length / 1 MiB; exit=the chunk sent
    while length > 0:
        piece = handle.read(min(length, 1024 * 1024))
        if not piece:
            return
        length -= len(piece)
        yield piece


async def main(tenant: str, file_path: str, root: str) -> None:
    from tests.documents.world import service

    os.environ["DEVSTORE_PUBLIC_URL"] = PUBLIC
    os.environ["DEVSTORE_ROOT"] = root
    svc = service(Path(root))
    t, path = UUID(tenant), Path(file_path)
    started = time.monotonic()
    opened = await svc.start_upload(t, "user-1", path.name, path.stat().st_size, None, None)
    assert opened["protocol"] == "resumable"
    await send(opened["upload_url"], path, int(opened["chunk_bytes"]))
    done = await svc.complete(t, UUID(opened["document_id"]))
    uploaded = time.monotonic()
    after_upload = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    status = await svc.process(t, UUID(opened["document_id"]))
    processed = time.monotonic()
    view = await svc.status(t, UUID(opened["document_id"]))
    print(
        json.dumps(
            {
                "status": status,
                "completed": done["status"],
                "parts": view["parts_total"],
                "parts_done": view["parts_done"],
                "passages": view["passages"],
                "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
                "peak_after_upload_mb": after_upload,
                "child_peak_rss_mb": round(
                    resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
                ),
                "upload_s": round(uploaded - started, 1),
                "read_s": round(processed - uploaded, 1),
                "document_id": opened["document_id"],
            }
        )
    )


if __name__ == "__main__":
    from tests.scope import act_as_test_system

    act_as_test_system()
    asyncio.run(main(*sys.argv[1:4]))
