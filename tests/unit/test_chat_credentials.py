"""Tenant model keys are encrypted with tenant-bound associated data and safe errors."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from account_intelligence.chat.credentials import TenantKeyUnavailable, TenantModelKeyStore


@pytest.mark.asyncio
async def test_kms_encrypt_and_decrypt_bind_ciphertext_to_tenant() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeKms:
        def encrypt(self, *, request: dict[str, object]) -> SimpleNamespace:
            calls.append(("encrypt", request))
            return SimpleNamespace(ciphertext=b"ciphertext-only")

        def decrypt(self, *, request: dict[str, object]) -> SimpleNamespace:
            calls.append(("decrypt", request))
            return SimpleNamespace(plaintext=b"sk-ant-api03-synthetic-secret")

    tenant = UUID("11111111-1111-4111-8111-111111111111")
    store = TenantModelKeyStore("projects/p/locations/r/keyRings/k/cryptoKeys/byok", FakeKms())
    encrypted = await store.encrypt(tenant, "sk-ant-api03-synthetic-secret")
    plaintext = await store.decrypt(tenant, encrypted)

    assert encrypted == b"ciphertext-only"
    assert plaintext == "sk-ant-api03-synthetic-secret"
    assert calls[0][1]["plaintext"] == b"sk-ant-api03-synthetic-secret"
    assert calls[0][1]["additional_authenticated_data"] == (
        b"account-intelligence:anthropic:11111111-1111-4111-8111-111111111111"
    )
    assert (
        calls[1][1]["additional_authenticated_data"] == calls[0][1]["additional_authenticated_data"]
    )


@pytest.mark.asyncio
async def test_missing_kms_key_fails_closed_without_echoing_a_secret() -> None:
    store = TenantModelKeyStore(None)
    with pytest.raises(TenantKeyUnavailable) as error:
        await store.encrypt(UUID(int=1), "sk-ant-api03-synthetic-secret")
    assert "synthetic-secret" not in str(error.value)
