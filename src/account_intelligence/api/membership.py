"""Tenant-scoped membership lookup with a short revocation cache.

Guarantees: identity claims are accepted only when the membership role still
matches the database (GUARANTEE(test_membership_cache_rechecks_expired_membership)).
Database failures are not cached.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from account_intelligence.db.session import tenant_txn
from tenancy.keys import cache_key


class MembershipPort(Protocol):
    """Database-facing membership lookup contract."""

    async def role_for(self, tenant_id: UUID, user_id: str) -> str | None:
        """Return the subject's current role in the tenant, or None when absent."""
        ...


class DatabaseMembershipPort(MembershipPort):
    """Membership lookup using the same tenant transaction/RLS boundary as API reads: the role
    of an ACTIVE member of a tenant that is not suspended (``identity.membership``)."""

    async def role_for(self, tenant_id: UUID, user_id: str) -> str | None:
        """Look the role up inside the tenant transaction so RLS scopes the query."""
        from identity.membership import active_role

        async with tenant_txn(tenant_id) as session:
            return await active_role(session, user_id)


@dataclass(frozen=True)
class MembershipEntry:
    """Cached role decision with the monotonic time it must be rechecked after."""

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
        self._entries: dict[str, MembershipEntry] = {}
        self._lock = asyncio.Lock()

    async def is_active(self, tenant_id: UUID, user_id: str, role: str) -> bool:
        """Check that the token's role matches the current tenant membership."""
        # the user id may contain ':'; its hash is a collision-free key part
        key = cache_key(tenant_id, "membership", hashlib.sha256(user_id.encode()).hexdigest())
        now = self._monotonic()
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None and now < entry.expires_at:
                return entry.role == role
            current_role = await self._port.role_for(tenant_id, user_id)
            self._entries[key] = MembershipEntry(current_role, now + self._ttl_seconds)
            return current_role == role

    def forget(self, tenant_id: UUID, user_id: str) -> None:
        """Drop one cached decision (after an access change in this process); other processes
        recheck within the TTL."""
        key = cache_key(tenant_id, "membership", hashlib.sha256(user_id.encode()).hexdigest())
        self._entries.pop(key, None)


def cache_of(request: Any) -> MembershipCache:
    """The application's membership cache (one per app; TTL from ``config/session.yaml``)."""
    cache = getattr(request.app.state, "membership_cache", None)
    if cache is None:
        from identity.sessions import load_policy

        cache = MembershipCache(
            DatabaseMembershipPort(), ttl_seconds=load_policy().membership_cache_s
        )
        request.app.state.membership_cache = cache
    return cache
