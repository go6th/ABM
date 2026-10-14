"""Streaming object storage for raw research files (never whole files in memory).

``BlobStore`` writes a part from an async byte stream, composes parts into one object while
hashing it, opens an object as a binary file for streaming reads, and deletes. Adapters:

* ``LocalBlobStore``: a directory (the dev ``payloads`` volume, tests). Writes go to a temporary
  file and are renamed into place, so a reader never sees half an object.
* ``GcsBlobStore``: Google Cloud Storage. VERIFY(V-FILES-1): written against the
  google-cloud-storage API (resumable upload from a stream, compose, ``Blob.open``) and not yet run
  against a bucket.

A browser upload goes straight to storage (owner decision 2026-10-14): ``resumable_upload`` opens
a resumable session (GCS's own, or the development object store's on the same directory, see
``devstore.server``) whose URL the browser sends chunks to; the API never sees the bytes.

Keys are neutral: ``tenants/<tenant>/files/...``; no file name ever appears in a key.

GUARANTEE(test_local_blob_store_streams_and_composes_without_holding_the_file)
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import IO, Any, Protocol

COPY_BUFFER = 1 << 20  # 1 MiB


class BlobMissing(KeyError):
    """No object under this key."""


class PartTooLarge(ValueError):
    """A part exceeded its declared maximum size."""


class BlobStore(Protocol):
    """See the module docstring."""

    async def write_stream(
        self, key: str, chunks: AsyncIterator[bytes], *, max_bytes: int
    ) -> tuple[int, str]:
        """Store the stream under ``key``; ``(size, sha256)``. ``PartTooLarge`` past ``max_bytes``
        (nothing is stored then)."""
        ...

    async def compose(self, key: str, parts: Sequence[str]) -> tuple[int, str]:
        """Concatenate ``parts`` into ``key`` in order; ``(size, sha256)`` of the result."""
        ...

    async def move(self, source: str, target: str) -> None:
        """Rename an object (no copy where the store allows it)."""
        ...

    def open(self, key: str) -> IO[bytes]:
        """A seekable binary reader over the object (``BlobMissing`` when absent)."""
        ...

    async def exists(self, key: str) -> bool:
        """Whether the object exists."""
        ...

    async def delete(self, key: str) -> None:
        """Remove the object (no error when absent)."""
        ...

    async def delete_prefix(self, prefix: str) -> int:
        """Remove every object under ``prefix``; the count removed."""
        ...

    async def resumable_upload(
        self, key: str, size: int, content_type: str, origin: str | None
    ) -> str | None:
        """A resumable upload session for exactly ``size`` bytes to ``key`` (the browser PUTs
        chunks to the URL), or None when the store has none."""
        ...

    async def size(self, key: str) -> int:
        """The object's size in bytes (``BlobMissing`` when absent)."""
        ...


class LocalBlobStore:
    """See the module docstring."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    def path(self, key: str) -> Path:
        """The file of ``key`` (refuses a key that escapes the root)."""
        target = (self.root / key).resolve()
        if self.root.resolve() not in target.parents:
            raise ValueError("object key escapes the store root")
        return target

    async def write_stream(
        self, key: str, chunks: AsyncIterator[bytes], *, max_bytes: int
    ) -> tuple[int, str]:
        """Write to a temporary file in the target directory, then rename."""
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".part-")
        try:
            with os.fdopen(fd, "wb") as out:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > max_bytes:
                        raise PartTooLarge(f"part exceeds {max_bytes} bytes")
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return size, digest.hexdigest()

    async def compose(self, key: str, parts: Sequence[str]) -> tuple[int, str]:
        """Concatenate with a fixed buffer, hashing as it goes."""
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".compose-")
        try:
            with os.fdopen(fd, "wb") as out:
                for part in parts:
                    try:
                        source = self.path(part).open("rb")
                    except FileNotFoundError:
                        raise BlobMissing(part) from None
                    with source:
                        # LOOP(blob compose copy): cap=the part's size in COPY_BUFFER reads;
                        # exit=end of part
                        while block := source.read(COPY_BUFFER):
                            digest.update(block)
                            size += len(block)
                            out.write(block)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return size, digest.hexdigest()

    async def move(self, source: str, target: str) -> None:
        """Atomic rename on the same volume."""
        dest = self.path(target)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(self.path(source), dest)
        except FileNotFoundError:
            raise BlobMissing(source) from None

    def open(self, key: str) -> IO[bytes]:
        """Open for reading."""
        try:
            return self.path(key).open("rb")
        except FileNotFoundError:
            raise BlobMissing(key) from None

    async def exists(self, key: str) -> bool:
        """Whether the file exists."""
        return self.path(key).is_file()

    async def resumable_upload(
        self, key: str, size: int, content_type: str, origin: str | None
    ) -> str | None:
        """A session of the development object store serving this directory
        (``DEVSTORE_PUBLIC_URL``); None without one (the API's content route stands in)."""
        del content_type, origin
        public = os.environ.get("DEVSTORE_PUBLIC_URL")
        if not public:
            return None
        from devstore.server import Store, ttl_s

        return Store(self.root, public, ttl_s()).open_session(key, size)

    async def size(self, key: str) -> int:
        """The file's size."""
        try:
            return self.path(key).stat().st_size
        except FileNotFoundError:
            raise BlobMissing(key) from None

    async def delete(self, key: str) -> None:
        """Unlink."""
        self.path(key).unlink(missing_ok=True)

    async def delete_prefix(self, prefix: str) -> int:
        """Remove every file under the prefix directory."""
        base = self.path(prefix.rstrip("/") + "/x").parent
        if not base.is_dir():
            return 0
        removed = 0
        for path in sorted(base.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
                removed += 1
            elif path.is_dir():
                path.rmdir()
        base.rmdir()
        return removed


class GcsBlobStore:
    """See the module docstring (VERIFY V-FILES-1)."""

    def __init__(self, client: Any, bucket: str) -> None:
        self.bucket = client.bucket(bucket)

    async def write_stream(
        self, key: str, chunks: AsyncIterator[bytes], *, max_bytes: int
    ) -> tuple[int, str]:
        """Spool the part to a bounded local temp file (a part is at most ``max_bytes``), then
        upload it in one request."""
        import asyncio

        digest = hashlib.sha256()
        size = 0
        with tempfile.TemporaryFile() as spool:
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise PartTooLarge(f"part exceeds {max_bytes} bytes")
                digest.update(chunk)
                spool.write(chunk)
            spool.seek(0)
            blob = self.bucket.blob(key)
            await asyncio.to_thread(blob.upload_from_file, spool, size=size)
        return size, digest.hexdigest()

    async def compose(self, key: str, parts: Sequence[str]) -> tuple[int, str]:
        """GCS compose (at most 32 sources per call: composed in rounds), then a streaming read to
        hash the result."""
        import asyncio

        sources = [self.bucket.blob(p) for p in parts]
        round_no = 0
        # LOOP(gcs compose rounds): cap=ceil(log32(parts)) rounds; exit=one source left
        while len(sources) > 32:
            merged = []
            for i in range(0, len(sources), 32):
                tmp = self.bucket.blob(f"{key}.compose-{round_no}-{i // 32}")
                await asyncio.to_thread(tmp.compose, sources[i : i + 32])
                merged.append(tmp)
            sources, round_no = merged, round_no + 1
        target = self.bucket.blob(key)
        await asyncio.to_thread(target.compose, sources)
        digest = hashlib.sha256()
        size = 0
        with target.open("rb") as reader:
            # LOOP(gcs compose hash read): cap=the object's size in COPY_BUFFER reads; exit=end
            while block := reader.read(COPY_BUFFER):
                digest.update(block)
                size += len(block)
        return size, digest.hexdigest()

    async def move(self, source: str, target: str) -> None:
        """Server-side rewrite, then delete the source."""
        import asyncio

        blob = self.bucket.blob(source)
        await asyncio.to_thread(self.bucket.copy_blob, blob, self.bucket, target)
        await asyncio.to_thread(blob.delete)

    def open(self, key: str) -> IO[bytes]:
        """A streaming, seekable reader."""
        blob = self.bucket.blob(key)
        if not blob.exists():
            raise BlobMissing(key)
        reader: IO[bytes] = blob.open("rb")
        return reader

    async def exists(self, key: str) -> bool:
        """Whether the object exists."""
        import asyncio

        return bool(await asyncio.to_thread(self.bucket.blob(key).exists))

    async def delete(self, key: str) -> None:
        """Delete when present."""
        import asyncio

        blob = self.bucket.blob(key)
        if await asyncio.to_thread(blob.exists):
            await asyncio.to_thread(blob.delete)

    async def resumable_upload(
        self, key: str, size: int, content_type: str, origin: str | None
    ) -> str | None:
        """A GCS resumable upload session URI for ``size`` bytes (VERIFY V-FILES-1). ``origin``
        is the web app's origin, so the browser's chunk PUTs pass CORS."""
        import asyncio

        blob = self.bucket.blob(key)
        url: str = await asyncio.to_thread(
            blob.create_resumable_upload_session,
            content_type=content_type,
            size=size,
            origin=origin,
        )
        return url

    async def size(self, key: str) -> int:
        """The object's size from its metadata."""
        import asyncio

        blob = await asyncio.to_thread(self.bucket.get_blob, key)
        if blob is None:
            raise BlobMissing(key)
        return int(blob.size)

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under the prefix."""
        import asyncio

        blobs = list(await asyncio.to_thread(lambda: list(self.bucket.list_blobs(prefix=prefix))))
        for blob in blobs:
            await asyncio.to_thread(blob.delete)
        return len(blobs)


def file_key(tenant_id: object, file_id: object) -> str:
    """The raw object of a file."""
    return f"tenants/{tenant_id}/files/{file_id}/raw"


def part_key(tenant_id: object, upload_id: object, part_no: int) -> str:
    """One uploaded part."""
    return f"tenants/{tenant_id}/files/uploads/{upload_id}/part-{part_no:06d}"


def upload_prefix(tenant_id: object, upload_id: object) -> str:
    """Every part of one upload."""
    return f"tenants/{tenant_id}/files/uploads/{upload_id}/"
