"""With the worker's read-only bucket mount (BLOB_MOUNT_DIR) the parser sandbox reads a document
in place: no whole-file temporary copy, which on Cloud Run would sit in memory."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_files.blobstore import GcsBlobStore


class Client:
    def bucket(self, name: str) -> object:
        return SimpleNamespace(name=name)


def test_the_mounted_store_hands_the_sandbox_the_object_in_place(tmp_path: Path) -> None:
    key = "tenants/t/documents/d/raw"
    (tmp_path / key).parent.mkdir(parents=True)
    (tmp_path / key).write_bytes(b"x")
    store = GcsBlobStore(Client(), "bucket", str(tmp_path))
    assert store.path(key) == (tmp_path / key).resolve()
    with pytest.raises(ValueError):
        store.path("../outside")
    from documents.service import DocumentService

    service = DocumentService.__new__(DocumentService)
    service.blobs = store

    async def where() -> str:
        async with service._local(key) as path:
            return path

    assert asyncio.run(where()) == str((tmp_path / key).resolve())


def test_without_a_mount_the_store_offers_no_local_path() -> None:
    assert not hasattr(GcsBlobStore(Client(), "bucket"), "path")
