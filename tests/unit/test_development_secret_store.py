"""The development secret store: a stable CSRF key for the process lifetime, nothing else."""

from __future__ import annotations

import pytest

from account_intelligence.api.tenant_plane import CSRF_KEY_REF, DevelopmentSecretStore
from account_intelligence.platform.ports import NotConfigured


@pytest.mark.asyncio
async def test_development_secret_store_provides_stable_csrf_key_for_process_lifetime() -> None:
    store = DevelopmentSecretStore()

    first = await store.read(CSRF_KEY_REF)
    assert len(first) >= 32
    assert await store.read(CSRF_KEY_REF) == first


@pytest.mark.asyncio
async def test_development_secret_store_supports_local_secrets_and_refuses_unknown_keys() -> None:
    store = DevelopmentSecretStore()

    assert await store.put("test/provider-secret", b"local-secret") == "test/provider-secret"
    assert await store.read("test/provider-secret") == b"local-secret"
    with pytest.raises(ValueError, match="already exists"):
        await store.put("test/provider-secret", b"replacement")

    await store.destroy("test/provider-secret")
    with pytest.raises(NotConfigured, match="not configured"):
        await store.read("test/provider-secret")
