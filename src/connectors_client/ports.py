"""The outbound boundary of the connectors: OAuth and the sources' read APIs.

No port has a method that writes to a tenant's CRM, Drive or mailbox (CN4: fetch is read-only by
construction). The production adapters do not exist yet: every ``Unconfigured*`` raises
``NotConfigured`` (no real OAuth app is registered, D-65), never a fake success.

GUARANTEE(test_connector_ports_are_read_only_and_fail_closed)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from account_intelligence.platform.ports import NotConfigured


class TokenRevoked(RuntimeError):
    """The source refused the grant (revoked or expired refresh token)."""


class SourceError(RuntimeError):
    """A source call failed for a reason other than the grant; carries a fixed code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TokenSet:
    """What a token exchange or refresh returns. Never logged."""

    access_token: str
    refresh_token: str | None
    scopes: tuple[str, ...]
    expires_in: int


@dataclass(frozen=True)
class DriveMeta:
    """A Drive file's identity and ancestry facts."""

    id: str
    name: str
    mime_type: str
    parents: tuple[str, ...] = ()
    drive_id: str | None = None
    shortcut_target: str | None = None

    @property
    def is_folder(self) -> bool:
        """True for a folder."""
        return self.mime_type == "application/vnd.google-apps.folder"


@dataclass(frozen=True)
class Page:
    """One page of a listing."""

    items: tuple[Any, ...]
    next_token: str | None = None


@dataclass(frozen=True)
class CrmRecord:
    """One CRM record as returned (fields filtered by the caller)."""

    object: str
    id: str
    fields: dict[str, Any] = field(default_factory=dict)


class OAuthClient(Protocol):
    """A source's OAuth 2.0 endpoints."""

    async def exchange(
        self, kind: str, *, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet:
        """Exchange an authorization code (PKCE)."""
        ...

    async def refresh(self, kind: str, refresh_token: str) -> TokenSet:
        """A fresh access token (TokenRevoked when the grant is gone)."""
        ...

    async def revoke(self, kind: str, token: str) -> None:
        """Revoke the grant at the source."""
        ...


class ConnectorClient(Protocol):
    """A source's read API (no write method exists)."""

    async def list_folder(self, access_token: str, folder_id: str, page_token: str | None) -> Page:
        """One page of a Drive folder's children (DriveMeta items)."""
        ...

    async def file_meta(self, access_token: str, file_id: str) -> DriveMeta | None:
        """A Drive file's metadata, or None when it is not visible."""
        ...

    async def fetch(self, access_token: str, file_id: str, max_bytes: int) -> bytes:
        """A Drive file's content (SourceError ``too_large`` above max_bytes)."""
        ...

    async def list_records(
        self, access_token: str, obj: str, fields: tuple[str, ...], page_token: str | None
    ) -> Page:
        """One page of CRM records (CrmRecord items) with at most the named fields."""
        ...


class VaultIngestPort(Protocol):
    """The Phase B ingestion pipeline (parse, redact, embed) that takes items from quarantine."""

    async def ingest(self, tenant_id: str, item_key: str) -> None:
        """Take one quarantined item."""
        ...


class UnconfiguredOAuthClient:
    """No OAuth app is registered for any source yet."""

    async def exchange(
        self, kind: str, *, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet:
        """Refuse: not configured."""
        raise NotConfigured(f"{kind} OAuth app is not registered")

    async def refresh(self, kind: str, refresh_token: str) -> TokenSet:
        """Refuse: not configured."""
        raise NotConfigured(f"{kind} OAuth app is not registered")

    async def revoke(self, kind: str, token: str) -> None:
        """Refuse: not configured."""
        raise NotConfigured(f"{kind} OAuth app is not registered")


class UnconfiguredConnectorClient:
    """No source read adapter is built yet."""

    async def list_folder(self, access_token: str, folder_id: str, page_token: str | None) -> Page:
        """Refuse: not configured."""
        raise NotConfigured("Drive adapter is not configured")

    async def file_meta(self, access_token: str, file_id: str) -> DriveMeta | None:
        """Refuse: not configured."""
        raise NotConfigured("Drive adapter is not configured")

    async def fetch(self, access_token: str, file_id: str, max_bytes: int) -> bytes:
        """Refuse: not configured."""
        raise NotConfigured("Drive adapter is not configured")

    async def list_records(
        self, access_token: str, obj: str, fields: tuple[str, ...], page_token: str | None
    ) -> Page:
        """Refuse: not configured."""
        raise NotConfigured("CRM adapter is not configured")


class UnconfiguredVault:
    """Phase B is not built: quarantined items stay where they are."""

    async def ingest(self, tenant_id: str, item_key: str) -> None:
        """Refuse: not configured."""
        raise NotConfigured("the Phase B ingestion pipeline is not built")
