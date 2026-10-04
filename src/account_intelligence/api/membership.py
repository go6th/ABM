"""Tenant-scoped membership lookup with a short revocation cache.

Guarantees: identity claims are accepted only when the membership role still
matches the database (GUARANTEE(test_membership_cache_rechecks_expired_membership)).
Database failures are not cached.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import text

from account_intelligence.db.session import tenant_txn


class MembershipPort(Protocol):
    """Database-facing membership lookup contract."""

    async def role_for(self, tenant_id: UUID, user_id: str) -> str | None: ...


class DatabaseMembershipPort(MembershipPort):
    """Membership lookup using the same tenant transaction/RLS boundary as API reads."""

    async def role_for(self, tenant_id: UUID, user_id: str) -> str | None:
        async with tenant_txn(tenant_id) as session:
            return (
                await session.execute(
                    text("SELECT role FROM memberships WHERE user_id = :user_id"),
                    {"user_id": user_id},
                )
            ).scalar_one_or_none()


@dataclass(frozen=True)
class MembershipEntry:
    role: str | None
    expires_at: float


class MembershipCache:
    """Cache membership decisions for no longer than the configured TTL."""

    def __init__(
        self,
        port: MembershipPort,
        *,
        ttl_seconds: float = 60,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("membership cache TTL must be positive")
        self._port = port
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        self._entries: dict[tuple[UUID, str], MembershipEntry] = {}
        self._lock = asyncio.Lock()

    async def is_active(self, tenant_id: UUID, user_id: str, role: str) -> bool:
        """Check that the token's role matches the current tenant membership."""
        key = (tenant_id, user_id)
        now = self._monotonic()
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None and now < entry.expires_at:
                return entry.role == role
            current_role = await self._port.role_for(tenant_id, user_id)
            self._entries[key] = MembershipEntry(current_role, now + self._ttl_seconds)
            return current_role == role
