"""The Cloud Storage client keeps a connection pool as large as the reads that run side by side."""

from __future__ import annotations

from typing import Any

from account_intelligence.platform.gcp.adapters import GCS_POOL_SIZE, widen_pool


class _Session:
    def __init__(self) -> None:
        self.mounted: dict[str, Any] = {}

    def mount(self, prefix: str, adapter: Any) -> None:
        self.mounted[prefix] = adapter


class _Client:
    def __init__(self) -> None:
        self._http = _Session()


def test_the_https_adapter_holds_as_many_connections_as_the_reads_in_flight() -> None:
    client = _Client()
    assert widen_pool(client) is client
    adapter = client._http.mounted["https://"]
    assert adapter._pool_maxsize == GCS_POOL_SIZE and GCS_POOL_SIZE >= 32  # default was 10


def test_a_client_without_a_session_is_left_alone() -> None:
    class Bare:
        pass

    bare = Bare()
    assert widen_pool(bare) is bare
