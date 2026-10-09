"""Per-tenant provider-key envelope encryption and RLS-scoped ciphertext storage."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.db.session import tenant_txn


class TenantKeyUnavailable(RuntimeError):
    """The configured KMS key or stored tenant key could not be used safely."""


@dataclass(frozen=True)
class TenantKeyStatus:
    """Non-secret metadata returned by key management endpoints."""

    configured: bool
    updated_at: str | None


class TenantModelKeyStore:
    """Encrypt Anthropic API keys with Cloud KMS authenticated to the tenant identity."""

    def __init__(self, key_resource: str | None, client: Any | None = None) -> None:
        self._key_resource = key_resource
        self._client = client

    @property
    def configured(self) -> bool:
        """Whether this environment has a KMS key for tenant key management."""
        return bool(self._key_resource)

    def _kms(self) -> Any:
        if not self._key_resource:
            raise TenantKeyUnavailable("tenant model keys are not configured")
        if self._client is None:
            from google.cloud import kms_v1

            self._client = kms_v1.KeyManagementServiceClient()
        return self._client

    @staticmethod
    def _aad(tenant_id: UUID) -> bytes:
        """Bind ciphertext to exactly one tenant and provider."""
        return f"account-intelligence:anthropic:{tenant_id}".encode("ascii")

    async def encrypt(self, tenant_id: UUID, api_key: str) -> bytes:
        """KMS encrypt a validated key without returning plaintext to a data store."""
        client = self._kms()
        try:
            result = await asyncio.to_thread(
                client.encrypt,
                request={
                    "name": self._key_resource,
                    "plaintext": api_key.encode("utf-8"),
                    "additional_authenticated_data": self._aad(tenant_id),
                },
            )
        except Exception as exc:
            raise TenantKeyUnavailable("tenant model key encryption failed") from exc
        return bytes(result.ciphertext)

    async def decrypt(self, tenant_id: UUID, ciphertext: bytes) -> str:
        """KMS decrypt a tenant's key only in the API process, with tenant-bound AAD."""
        client = self._kms()
        try:
            result = await asyncio.to_thread(
                client.decrypt,
                request={
                    "name": self._key_resource,
                    "ciphertext": ciphertext,
                    "additional_authenticated_data": self._aad(tenant_id),
                },
            )
            value = bytes(result.plaintext).decode("utf-8")
            if not value.startswith("sk-ant-"):
                raise ValueError("decrypted value is not an Anthropic API key")
            return value
        except Exception as exc:
            raise TenantKeyUnavailable("tenant model key decryption failed") from exc


async def key_status(tenant_id: UUID) -> TenantKeyStatus:
    """Read non-secret status for the current tenant."""
    async with tenant_txn(tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT updated_at FROM tenant_model_credentials WHERE provider = 'anthropic'")
            )
        ).first()
    return TenantKeyStatus(
        configured=row is not None,
        updated_at=None if row is None else row.updated_at.isoformat(),
    )


async def encrypted_key_for_thread(tenant_id: UUID, user_id: str, thread_id: UUID) -> bytes | None:
    """Read key ciphertext only after confirming the thread belongs to this member."""
    async with tenant_txn(tenant_id) as session:
        owner = (
            await session.execute(
                text("SELECT 1 FROM chat_threads WHERE id = :t AND owner_user_id = :u"),
                {"t": thread_id, "u": user_id},
            )
        ).first()
        if owner is None:
            return None
        row = (
            await session.execute(
                text("SELECT ciphertext FROM tenant_model_credentials WHERE provider = 'anthropic'")
            )
        ).first()
    return None if row is None else bytes(row.ciphertext)


async def save_encrypted_key(session: AsyncSession, ciphertext: bytes, updated_by: str) -> str:
    """Upsert ciphertext only; caller includes the admin audit event in this transaction."""
    result = await session.execute(
        text(
            "INSERT INTO tenant_model_credentials (provider, ciphertext, updated_by)"
            " VALUES ('anthropic', :ciphertext, :actor)"
            " ON CONFLICT (tenant_id, provider) DO UPDATE SET"
            " ciphertext = EXCLUDED.ciphertext, updated_by = EXCLUDED.updated_by,"
            " updated_at = now() RETURNING updated_at"
        ),
        {"ciphertext": ciphertext, "actor": updated_by},
    )
    updated_at = cast(datetime, result.scalar_one())
    return updated_at.isoformat()


async def delete_encrypted_key(session: AsyncSession) -> bool:
    """Remove the tenant's stored ciphertext; caller audits atomically."""
    result = await session.execute(
        text("DELETE FROM tenant_model_credentials WHERE provider = 'anthropic' RETURNING provider")
    )
    return result.scalar_one_or_none() is not None
