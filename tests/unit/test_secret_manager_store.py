"""Tenant secrets in Secret Manager (tenant_plane.SecretManagerStore, audit 2026-10-15): a name
becomes an opaque tenant- secret with a new version per put, reads go by the version path,
destroy is idempotent, and platform secrets are read by name but can never be destroyed."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from account_intelligence.api.tenant_plane import SecretManagerStore
from account_intelligence.platform.ports import NotConfigured


class FakeClient:
    def __init__(self) -> None:
        self.secrets: dict[str, list[bytes | None]] = {}

    def create_secret(self, request: dict[str, Any]) -> None:
        path = f"{request['parent']}/secrets/{request['secret_id']}"
        if path in self.secrets:
            raise RuntimeError("409 Secret already exists")
        assert request["secret"]["labels"] == {"kind": "tenant-secret"}
        self.secrets[path] = []

    def add_secret_version(self, request: dict[str, Any]) -> Any:
        versions = self.secrets[request["parent"]]
        versions.append(request["payload"]["data"])
        return SimpleNamespace(name=f"{request['parent']}/versions/{len(versions)}")

    def access_secret_version(self, name: str) -> Any:
        path, version = name.rsplit("/versions/", 1)
        data = self.secrets[path][int(version) - 1]
        if data is None:
            raise RuntimeError("version is destroyed")
        return SimpleNamespace(payload=SimpleNamespace(data=data))

    def destroy_secret_version(self, name: str) -> None:
        path, version = name.rsplit("/versions/", 1)
        if self.secrets[path][int(version) - 1] is None:
            raise RuntimeError("FAILED_PRECONDITION: version is already destroyed")
        self.secrets[path][int(version) - 1] = None


class Source:
    def get(self, name: str) -> str:
        return f"platform:{name}"


def test_put_read_destroy_a_tenant_secret() -> None:
    client = FakeClient()
    store = SecretManagerStore(Source(), client, "979669242383")
    ref = asyncio.run(store.put("tenants/t1/connectors/c1", b'{"refresh_token": "r"}'))
    assert "/secrets/tenant-" in ref and "t1" not in ref and ref.endswith("/versions/1")
    assert asyncio.run(store.read(ref)) == b'{"refresh_token": "r"}'
    again = asyncio.run(store.put("tenants/t1/connectors/c1", b"rotated"))
    assert (
        again.endswith("/versions/2")
        and again.rsplit("/versions/", 1)[0] == ref.rsplit("/versions/", 1)[0]
    )
    asyncio.run(store.destroy(ref))
    asyncio.run(store.destroy(ref))  # idempotent
    with pytest.raises(NotConfigured):
        asyncio.run(store.read(ref))
    assert asyncio.run(store.read(again)) == b"rotated"


def test_platform_secrets_are_read_by_name_and_never_destroyed() -> None:
    store = SecretManagerStore(Source(), FakeClient(), "979669242383")
    assert asyncio.run(store.read("csrf-signing-key")) == b"platform:csrf-signing-key"
    for ref in ("csrf-signing-key", "projects/979669242383/secrets/anthropic-api-key/versions/1"):
        with pytest.raises(NotConfigured):
            asyncio.run(store.destroy(ref))
