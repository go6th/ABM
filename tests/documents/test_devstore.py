"""The development object store speaks the resumable-upload protocol the browser uses (Phase 4B
section 3): chunks with ``Content-Range``, ``308`` with the committed range, a status query after
a drop, and the finished object in the blob store's directory."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from devstore.server import Store, build

pytestmark = pytest.mark.asyncio
CHUNK = 256 * 1024


def _client(root: Path) -> httpx.AsyncClient:
    app = build(root, "http://store.test")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://store.test")


def _path(url: str) -> str:
    return url.removeprefix("http://store.test")


async def _put(
    c: httpx.AsyncClient, url: str, data: bytes, start: int, total: int
) -> httpx.Response:
    end = start + len(data) - 1
    return await c.put(
        _path(url), content=data, headers={"Content-Range": f"bytes {start}-{end}/{total}"}
    )


async def test_a_dropped_upload_resumes_from_the_committed_offset(tmp_path: Path) -> None:
    data = os.urandom(CHUNK * 3 + 1234)
    total = len(data)
    url = Store(tmp_path, "http://store.test").open_session("tenants/t/quarantine/d/raw", total)
    async with _client(tmp_path) as c:
        first = await _put(c, url, data[:CHUNK], 0, total)
        assert first.status_code == 308 and first.headers["range"] == f"bytes=0-{CHUNK - 1}"
        # the page reloads: the uploader asks what is committed and continues from there
        asked = await c.put(_path(url), headers={"Content-Range": f"bytes */{total}"})
        assert asked.status_code == 308 and asked.headers["range"] == f"bytes=0-{CHUNK - 1}"
        # a retried first chunk (its answer was lost) changes nothing
        again = await _put(c, url, data[:CHUNK], 0, total)
        assert again.headers["range"] == f"bytes=0-{CHUNK - 1}"
        # a chunk past the committed offset is refused: nothing may be skipped
        gap = await _put(c, url, data[2 * CHUNK : 3 * CHUNK], 2 * CHUNK, total)
        assert gap.status_code == 400
        # a chunk that overlaps the committed part writes only the new bytes
        overlap = await _put(c, url, data[CHUNK - 100 : 2 * CHUNK], CHUNK - 100, total)
        assert overlap.headers["range"] == f"bytes=0-{2 * CHUNK - 1}"
        last = await _put(c, url, data[2 * CHUNK :], 2 * CHUNK, total)
        assert last.status_code == 200 and last.json() == {"size": total}
        done = await c.put(_path(url), headers={"Content-Range": f"bytes */{total}"})
        assert done.status_code == 200
    assert (tmp_path / "tenants/t/quarantine/d/raw").read_bytes() == data
    assert not list((tmp_path / ".sessions").glob("*.part"))


async def test_the_store_refuses_bad_sessions_ranges_and_keys(tmp_path: Path) -> None:
    store = Store(tmp_path, "http://store.test")
    with pytest.raises(ValueError):
        store.open_session("../outside", 10)
    url = store.open_session("tenants/t/x", 10)
    async with _client(tmp_path) as c:
        assert (await c.put("/upload/" + "a" * 43, content=b"x")).status_code == 404
        assert (await c.put(_path(url), content=b"x")).status_code == 400  # no Content-Range
        long = await c.put(
            _path(url), content=b"0123456789AB", headers={"Content-Range": "bytes 0-9/10"}
        )
        assert long.status_code == 400
        assert store.committed(url.rsplit("/", 1)[1]) == 0
        wrong_total = await _put(c, url, b"0123456789", 0, 11)
        assert wrong_total.status_code == 400


async def test_cors_answers_only_the_allowed_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEVSTORE_ALLOW_ORIGIN", "http://localhost:3010")
    url = Store(tmp_path, "http://store.test").open_session("tenants/t/y", 4)
    async with _client(tmp_path) as c:
        ok = await c.options(_path(url), headers={"Origin": "http://localhost:3010"})
        assert ok.headers["access-control-allow-origin"] == "http://localhost:3010"
        assert "Range" in ok.headers["access-control-expose-headers"]
        other = await c.options(_path(url), headers={"Origin": "http://evil.test"})
        assert "access-control-allow-origin" not in other.headers
