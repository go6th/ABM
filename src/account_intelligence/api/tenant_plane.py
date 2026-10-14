"""The tenant plane's collaborators, held by the container (one wiring point).

``TenantPlane`` carries the identity provider port, the secret store (OAuth tokens, signing and
CSRF keys), the object store, the mailer, the malware scanner, the clock and the public base URL
the IdP redirect URIs are built from. ``unconfigured_plane`` is the production default until real
adapters exist: every port raises ``NotConfigured`` and the routes that need one answer 503 (fail
closed), never a fake success.

GUARANTEE(test_unconfigured_tenant_plane_fails_closed)
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from typing import Any

from account_intelligence.platform.ports import Clock, NotConfigured
from identity.broker import Broker
from identity.ports import IdentityProvider, OidcEndpoints
from identity.sessions import SessionPolicy, load_policy
from tenancy.ports import (
    Mailer,
    ObjectStore,
    SecretStore,
    UnconfiguredMailer,
    UnconfiguredObjectStore,
    UnconfiguredSecretStore,
)

# Secret ids, as Secret Manager accepts them (letters, digits, "-" and "_" only; audit M5: the
# earlier "tenant-plane/csrf-key" was refused there, so every cookie write would have been a 503).
# The CSRF key's id is CSRF_KEY_SECRET (the registry's default is the Terraform secret's name).
SECRET_ID = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
CSRF_KEY_REF = os.environ.get("CSRF_KEY_SECRET") or "csrf-signing-key"
DOWNLOAD_KEY_REF = "download-signing-key"


class UnconfiguredIdentityProvider:
    """Production stand-in until the HTTPS adapter is wired with a client and egress policy."""

    async def exchange_code(
        self,
        conn: OidcEndpoints,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_secret: str | None,
    ) -> str:
        """Refuse: no identity provider adapter is configured."""
        raise NotConfigured("identity provider adapter is not configured")

    async def jwks(self, conn: OidcEndpoints) -> dict[str, Any]:
        """Refuse: no identity provider adapter is configured."""
        raise NotConfigured("identity provider adapter is not configured")


class UnconfiguredScanner:
    """Production stand-in for the malware scanner (``spend.upload.ScanPort`` shape)."""

    async def scan(self, key: str) -> bool:
        """Refuse: no scanner is configured."""
        raise NotConfigured("scanner is not configured")


class DevelopmentSecretStore:
    """Process-local secrets for the hermetic dev/test API; values never survive a restart."""

    def __init__(self) -> None:
        self._values = {CSRF_KEY_REF: secrets.token_bytes(32)}

    async def put(self, name: str, value: bytes) -> str:
        """Store a new local secret reference without replacing an existing value."""
        if not name or not value:
            raise ValueError("secret name and value must be non-empty")
        if name in self._values:
            raise ValueError("secret reference already exists")
        self._values[name] = bytes(value)
        return name

    async def read(self, ref: str) -> bytes:
        """Read a local secret or fail closed when the reference is unknown."""
        try:
            return self._values[ref]
        except KeyError:
            raise NotConfigured(f"local secret {ref} is not configured") from None

    async def destroy(self, ref: str) -> None:
        """Remove a local secret reference."""
        self._values.pop(ref, None)


@dataclass
class TenantPlane:
    """Everything the tenant-plane routes need beyond the database."""

    idp: IdentityProvider
    secrets: SecretStore
    objects: ObjectStore
    mailer: Mailer
    scanner: Any
    clock: Clock
    base_url: str | None
    policy: SessionPolicy
    oauth: Any = None
    connectors: Any = None
    connector_client_ids: dict[str, str] | None = None
    vault: Any = None
    staff_verifier: Any = None

    def connector_ports(self) -> tuple[Any, Any, Any]:
        """(OAuth client, read client, Phase B vault), fail-closed defaults when unwired."""
        from connectors_client.ports import (
            UnconfiguredConnectorClient,
            UnconfiguredOAuthClient,
            UnconfiguredVault,
        )

        return (
            self.oauth or UnconfiguredOAuthClient(),
            self.connectors or UnconfiguredConnectorClient(),
            self.vault or UnconfiguredVault(),
        )

    def connector_client_id(self, kind: str) -> str:
        """The registered OAuth client id of a source (NotConfigured: no app is registered)."""
        found = (self.connector_client_ids or {}).get(kind)
        if not found:
            raise NotConfigured(f"no OAuth app is registered for {kind}")
        return found

    async def key(self, ref: str) -> bytes:
        """A signing key from the secret store (NotConfigured when absent or too short)."""
        value = await self.secrets.read(ref)
        if len(value) < 32:
            raise NotConfigured(f"{ref} must be at least 32 bytes")
        return value

    def broker(self) -> Broker:
        """The identity broker over the shared database sessions."""
        from account_intelligence.db.session import tenant_txn, untenanted_session

        if not self.base_url:
            raise NotConfigured("PUBLIC_BASE_URL is not configured")
        return Broker(
            txn=tenant_txn,
            plain=untenanted_session,
            idp=self.idp,
            secrets=self.secrets,
            base_url=self.base_url.rstrip("/"),
            policy=self.policy,
        )


def unconfigured_plane(clock: Clock, base_url: str | None = None) -> TenantPlane:
    """The fail-closed default."""
    return TenantPlane(
        idp=UnconfiguredIdentityProvider(),
        secrets=UnconfiguredSecretStore(),
        objects=UnconfiguredObjectStore(),
        mailer=UnconfiguredMailer(),
        scanner=UnconfiguredScanner(),
        clock=clock,
        base_url=base_url,
        policy=load_policy(),
    )


class ReadOnlySecretStore:
    """Secret reads through the platform's secret source (Secret Manager in the cloud); writes
    and destruction stay NotConfigured until the write adapter exists (IdP client secrets and
    connector tokens therefore cannot be stored yet: fail closed)."""

    def __init__(self, source: Any) -> None:
        self._source = source

    async def read(self, ref: str) -> bytes:
        """The latest version of ``ref``."""
        import asyncio

        value = await asyncio.to_thread(self._source.get, ref)
        return str(value).encode()

    async def put(self, name: str, value: bytes) -> str:
        """Refuse: the secret write adapter is not built."""
        raise NotConfigured("secret store write adapter is not configured")

    async def destroy(self, ref: str) -> None:
        """Refuse: the secret write adapter is not built."""
        raise NotConfigured("secret store destroy adapter is not configured")


def cloud_plane(secret_source: Any, clock: Clock, base_url: str | None) -> TenantPlane:
    """The staging and production tenant plane: the HTTPS identity provider adapter (SSRF
    guarded), signing keys read from Secret Manager, everything else fail closed."""
    import httpx

    from identity.ports import HttpIdentityProvider

    plane = unconfigured_plane(clock, base_url)
    plane.idp = HttpIdentityProvider(httpx.AsyncClient(follow_redirects=False))
    plane.secrets = ReadOnlySecretStore(secret_source)
    return plane


async def assert_signing_keys(plane: TenantPlane) -> None:
    """At API start in staging and production (audit M5): the CSRF key's secret id is one Secret
    Manager accepts and the key reads back with 32 or more bytes, so a missing or misnamed secret
    stops the deploy instead of failing every cookie write with 503."""
    if not SECRET_ID.match(CSRF_KEY_REF):
        raise NotConfigured("CSRF_KEY_SECRET is not a valid Secret Manager secret id")
    await plane.key(CSRF_KEY_REF)


def plane_of(request: Any) -> TenantPlane:
    """The container's tenant plane, or the fail-closed default when none is wired."""
    container = request.app.state.container
    plane = getattr(container, "tenant_plane", None)
    if plane is None:
        from account_intelligence.config import get_settings

        plane = unconfigured_plane(container.clock, get_settings().public_base_url)
        container.tenant_plane = plane
    return plane
