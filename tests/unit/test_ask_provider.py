"""Ask selects the configured provider and a saved workspace Anthropic key when present."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from account_intelligence.api.routes import ask as ask_route
from account_intelligence.chat.credentials import TenantModelKeyStore

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")


def _anthropic_model_id() -> str:
    return next(
        model_id
        for model_id, choice in ((choice.id, choice) for choice in ask_route.catalogue().models)
        if choice.provider == "anthropic" and "fable" in choice.name.lower()
    )


@pytest.mark.asyncio
async def test_ask_uses_saved_workspace_key_for_selected_claude_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_provider = object()
    tenant_client = object()
    tenant_provider = object()
    decrypted: list[tuple[UUID, bytes]] = []
    api_keys: list[str] = []

    class KeyStore(TenantModelKeyStore):
        def __init__(self) -> None:
            super().__init__("kms-key")

        async def decrypt(self, tenant_id: UUID, ciphertext: bytes) -> str:
            decrypted.append((tenant_id, ciphertext))
            return "sk-ant-tenant-key"

    class Provider:
        def __init__(self, providers: dict[str, object]) -> None:
            self.providers = providers

        def for_model(self, provider: str) -> object | None:
            return self.providers.get(provider)

    monkeypatch.setattr(
        ask_route,
        "encrypted_key_for_tenant",
        lambda _tenant_id: _async_result(b"ciphertext"),
    )
    monkeypatch.setattr(
        ask_route,
        "anthropic_api_client",
        lambda api_key: api_keys.append(api_key) or tenant_client,
    )
    monkeypatch.setattr(
        ask_route,
        "AnthropicChatProvider",
        lambda client, *, effort: tenant_provider,
    )
    monkeypatch.setattr(ask_route, "chat_effort", lambda: "high")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                chat_providers=Provider({"anthropic": shared_provider}),
                tenant_model_keys=KeyStore(),
            )
        )
    )

    provider, client = await ask_route._provider_for_ask(
        request, SimpleNamespace(provider=shared_provider), _anthropic_model_id(), TENANT_ID
    )

    assert provider is tenant_provider
    assert client is tenant_client
    assert decrypted == [(TENANT_ID, b"ciphertext")]
    assert api_keys == ["sk-ant-tenant-key"]


@pytest.mark.asyncio
async def test_ask_uses_platform_key_when_no_workspace_key_is_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_provider = object()
    monkeypatch.setattr(
        ask_route,
        "encrypted_key_for_tenant",
        lambda _tenant_id: _async_result(None),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(chat_providers=None)))

    provider, client = await ask_route._provider_for_ask(
        request, SimpleNamespace(provider=shared_provider), _anthropic_model_id(), TENANT_ID
    )

    assert provider is shared_provider
    assert client is None


@pytest.mark.asyncio
async def test_ask_keeps_kimi_on_its_selected_provider_without_anthropic_key_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kimi_provider = object()
    monkeypatch.setattr(
        ask_route,
        "encrypted_key_for_tenant",
        lambda _tenant_id: pytest.fail("Kimi must not read an Anthropic key"),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                chat_providers=SimpleNamespace(
                    for_model=lambda provider: kimi_provider if provider == "moonshot" else None
                )
            )
        )
    )

    provider, client = await ask_route._provider_for_ask(
        request, SimpleNamespace(provider=object()), "kimi-k3", TENANT_ID
    )

    assert provider is kimi_provider
    assert client is None


async def _async_result(value: bytes | None) -> bytes | None:
    return value
