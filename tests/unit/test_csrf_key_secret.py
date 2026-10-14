"""The CSRF signing key's Secret Manager id is valid and checked at API start (audit M5)."""

from __future__ import annotations

import asyncio

import pytest

from account_intelligence.api.tenant_plane import (
    CSRF_KEY_REF,
    DOWNLOAD_KEY_REF,
    SECRET_ID,
    ReadOnlySecretStore,
    assert_signing_keys,
    unconfigured_plane,
)
from account_intelligence.config import Settings
from account_intelligence.platform.ports import NotConfigured


class Source:
    """A Secret Manager stand-in: ``values`` by secret id, anything else is not readable."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.asked: list[str] = []

    def get(self, name: str) -> str:
        self.asked.append(name)
        if name not in self.values:
            raise NotConfigured(f"secret {name} is not readable")
        return self.values[name]


def _plane(values: dict[str, str]) -> object:
    plane = unconfigured_plane(None, "https://app.example")  # type: ignore[arg-type]
    plane.secrets = ReadOnlySecretStore(Source(values))
    return plane


def test_the_key_ids_are_valid_secret_manager_ids_and_match_the_registry() -> None:
    assert SECRET_ID.match(CSRF_KEY_REF) and SECRET_ID.match(DOWNLOAD_KEY_REF)
    assert CSRF_KEY_REF == Settings.model_fields["csrf_key_secret"].default == "csrf-signing-key"
    assert not SECRET_ID.match("tenant-plane/csrf-key")  # the id that Secret Manager refused


def test_a_missing_or_short_key_stops_the_start_and_a_good_one_passes() -> None:
    asyncio.run(assert_signing_keys(_plane({CSRF_KEY_REF: "k" * 32})))  # type: ignore[arg-type]
    for values in ({}, {CSRF_KEY_REF: "short"}):
        with pytest.raises(NotConfigured):
            asyncio.run(assert_signing_keys(_plane(values)))  # type: ignore[arg-type]
