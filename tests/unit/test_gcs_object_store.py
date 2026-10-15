"""The cloud tenant object store and its scanner (tenant_plane.GcsObjectStore, ObjectScanner;
audit 2026-10-15): a key is written once, identical bytes again are a no-op, different bytes are
refused, listing stays under the prefix, and the scanner fails closed when unavailable."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from account_intelligence.api.tenant_plane import GcsObjectStore, ObjectScanner
from account_intelligence.platform.ports import NotConfigured


class Blob:
    def __init__(self, store: dict[str, bytes], name: str) -> None:
        self.store, self.name = store, name

    def upload_from_string(self, body: bytes, content_type: str, if_generation_match: int) -> None:
        assert if_generation_match == 0
        if self.name in self.store:
            raise RuntimeError("412 Precondition Failed")
        self.store[self.name] = body

    def download_as_bytes(self) -> bytes:
        return self.store[self.name]


class Bucket:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def blob(self, name: str) -> Blob:
        return Blob(self.store, name)

    def list_blobs(self, prefix: str, max_results: int) -> list[Any]:
        names = sorted(n for n in self.store if n.startswith(prefix))[:max_results]
        return [SimpleNamespace(name=n) for n in names]


class Client:
    def __init__(self) -> None:
        self.b = Bucket()

    def bucket(self, name: str) -> Bucket:
        return self.b


def test_objects_are_written_once_and_never_overwritten() -> None:
    store = GcsObjectStore(Client(), "payloads")
    key = "tenants/t1/branding/logo.png"
    assert asyncio.run(store.put_if_absent(key, b"png", "image/png")) is True
    assert asyncio.run(store.put_if_absent(key, b"png", "image/png")) is False
    with pytest.raises(FileExistsError):
        asyncio.run(store.put_if_absent(key, b"other", "image/png"))
    assert asyncio.run(store.get(key)) == b"png"
    asyncio.run(store.put_if_absent("tenants/t2/x", b"y", "text/plain"))
    assert asyncio.run(store.list_prefix("tenants/t1/", 10)) == [key]


@pytest.mark.parametrize(("result", "clean"), [("clean", True), ("infected", False)])
def test_the_scanner_reads_the_object_and_returns_the_verdict(result: str, clean: bool) -> None:
    store = GcsObjectStore(Client(), "payloads")
    asyncio.run(store.put_if_absent("k", b"bytes", "application/octet-stream"))

    class Scanner:
        async def scan(self, stream: Any) -> Any:
            assert stream.read() == b"bytes"
            return SimpleNamespace(result=result)

    assert asyncio.run(ObjectScanner(store, Scanner()).scan("k")) is clean


def test_an_unavailable_scanner_refuses() -> None:
    store = GcsObjectStore(Client(), "payloads")
    asyncio.run(store.put_if_absent("k", b"bytes", "application/octet-stream"))

    class Down:
        async def scan(self, stream: Any) -> Any:
            return SimpleNamespace(result="unavailable")

    with pytest.raises(NotConfigured):
        asyncio.run(ObjectScanner(store, Down()).scan("k"))
