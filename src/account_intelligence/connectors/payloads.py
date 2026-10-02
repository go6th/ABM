"""Raw provider payload storage under neutral keys.

Key: ``tenants/<tenant>/accounts/<account>/runs/<run>/raw/<sha256 of body>.json.gz``.
No provider code or vendor name appears in a key or file name; provider identity
lives only in the internal ledger. A put is a single atomic write (one S3 PUT, or a
dict assignment), so a cancelled task never leaves a half-written payload.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Protocol
from uuid import UUID


def payload_key(tenant_id: UUID, account_id: UUID, run_id: UUID, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    return f"tenants/{tenant_id}/accounts/{account_id}/runs/{run_id}/raw/{digest}.json.gz"


class PayloadStore(Protocol):
    async def put(self, key: str, body: bytes) -> str: ...
    async def get(self, key: str) -> bytes: ...


class InMemoryPayloadStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, body: bytes) -> str:
        self.objects[key] = gzip.compress(body, mtime=0)  # one assignment: atomic
        return key

    async def get(self, key: str) -> bytes:
        return gzip.decompress(self.objects[key])


class S3PayloadStore:
    """boto3 client injected; calls run in a worker thread. One PUT per object."""

    def __init__(self, client: object, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def put(self, key: str, body: bytes) -> str:
        data = gzip.compress(body, mtime=0)
        await asyncio.to_thread(
            self._client.put_object,  # type: ignore[attr-defined]
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType="application/json",
            ContentEncoding="gzip",
        )
        return key

    async def get(self, key: str) -> bytes:
        get_object = self._client.get_object  # type: ignore[attr-defined]
        response = await asyncio.to_thread(get_object, Bucket=self._bucket, Key=key)
        return gzip.decompress(response["Body"].read())


class LocalPayloadStore:
    """Filesystem store for development and multi-process tests.

    Atomic: the object is written to a temporary file in the same directory, flushed,
    then renamed over the final name, so a reader never sees a partial payload.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root.resolve() not in path.parents:
            raise ValueError("payload key escapes the store root")
        return path

    async def put(self, key: str, body: bytes) -> str:
        await asyncio.to_thread(self._write, self._path(key), gzip.compress(body, mtime=0))
        return key

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    async def get(self, key: str) -> bytes:
        data = await asyncio.to_thread(self._path(key).read_bytes)
        return gzip.decompress(data)
