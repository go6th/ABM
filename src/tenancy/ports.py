"""Ports of the tenant plane that have no production adapter yet, and their fail-closed stand-ins.

``Clock`` and ``NotConfigured`` are the canonical platform ones (``platform.ports``); malware
scanning is the existing ``spend.upload.ScanPort`` (one implementation per rule). This module adds:

* ``SecretStore``: write-once secret material behind a reference (OAuth tokens, signing keys,
  bring-your-own broker keys). Values never reach logs, audit rows, responses or a model.
* ``ObjectStore``: tenant objects with put-if-absent, read and prefix listing.
* ``Mailer``: transactional mail (invitations, deletion confirmations); identifiers in, no content
  beyond a fixed template's parameters.

Each ``Unconfigured*`` adapter raises ``NotConfigured`` on every call: a missing production
adapter is a typed failure, never a silent success (GUARANTEE(test_unconfigured_ports_fail_closed)).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from account_intelligence.platform.ports import NotConfigured


class SecretStore(Protocol):
    """Secret material behind opaque references."""

    async def put(self, name: str, value: bytes) -> str:
        """Store ``value`` as a new secret version; returns its reference (never the value)."""
        ...

    async def read(self, ref: str) -> bytes:
        """The secret behind ``ref`` (an error when missing or destroyed)."""
        ...

    async def destroy(self, ref: str) -> None:
        """Destroy the secret behind ``ref`` (idempotent)."""
        ...


class ObjectStore(Protocol):
    """Tenant objects under ``tenancy.storage`` keys. Storage never overwrites."""

    async def put_if_absent(self, key: str, body: bytes, content_type: str) -> bool:
        """Store ``body`` at ``key``; False when identical bytes already exist there, an error
        when different bytes do."""
        ...

    async def get(self, key: str) -> bytes:
        """The object's bytes (an error when missing)."""
        ...

    async def list_prefix(self, prefix: str, limit: int) -> list[str]:
        """At most ``limit`` keys under ``prefix`` (callers pass a tenant prefix)."""
        ...


class Mailer(Protocol):
    """Transactional mail from fixed templates."""

    async def send(self, to: str, template: str, params: Mapping[str, str]) -> str:
        """Queue one message; returns the provider's message reference."""
        ...


class UnconfiguredSecretStore:
    """Production stand-in until the Secret Manager write adapter is built."""

    async def put(self, name: str, value: bytes) -> str:
        """Refuse: no secret store is configured."""
        raise NotConfigured("secret store write adapter is not configured")

    async def read(self, ref: str) -> bytes:
        """Refuse: no secret store is configured."""
        raise NotConfigured("secret store read adapter is not configured")

    async def destroy(self, ref: str) -> None:
        """Refuse: no secret store is configured."""
        raise NotConfigured("secret store destroy adapter is not configured")


class UnconfiguredObjectStore:
    """Production stand-in until the tenant object adapter (Cloud Storage, CMEK) is built."""

    async def put_if_absent(self, key: str, body: bytes, content_type: str) -> bool:
        """Refuse: no object store is configured."""
        raise NotConfigured("object store is not configured")

    async def get(self, key: str) -> bytes:
        """Refuse: no object store is configured."""
        raise NotConfigured("object store is not configured")

    async def list_prefix(self, prefix: str, limit: int) -> list[str]:
        """Refuse: no object store is configured."""
        raise NotConfigured("object store is not configured")


class UnconfiguredMailer:
    """Production stand-in until a mail provider is chosen."""

    async def send(self, to: str, template: str, params: Mapping[str, str]) -> str:
        """Refuse: no mailer is configured."""
        raise NotConfigured("mailer is not configured")
