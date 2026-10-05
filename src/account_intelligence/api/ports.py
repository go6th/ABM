"""Typed boundaries between HTTP policy and owning platform services.

Guarantee: absent services fail closed (GUARANTEE(test_missing_service_is_not_success)).
No protocol accepts a tenant from an untrusted request field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

Role = Literal["admin", "seller", "viewer"]


@dataclass(frozen=True)
class Principal:
    """Verified caller identity; only produced by the identity verifier, never by requests."""

    subject: str
    tenant_id: UUID
    role: Role
    principal_type: Literal["jwt", "api_key", "operator_support"] = "jwt"


@dataclass(frozen=True)
class RunReceipt:
    """Client-safe projection of a run's identity, status and creation time."""

    run_id: UUID
    status: str
    created_at: datetime


@dataclass(frozen=True)
class RateLimitDecision:
    """Limiter outcome carrying the bounded retry window a 429 must report."""

    allowed: bool
    retry_after_seconds: int


@dataclass(frozen=True)
class DeliveredVersion:
    """One delivered artifact version with the hash and gate status it shipped with."""

    artifact_id: UUID
    name: str
    kind: str
    size_bytes: int
    sha256: str
    version: int
    gate_status: str
    delivered_at: datetime


class IdentityVerifier(Protocol):
    """Credential verification boundary so routes never trust raw token claims."""

    async def verify(self, credential: str) -> Principal:
        """Verify a bearer credential and return its principal, or raise."""
        ...


class MembershipPort(Protocol):
    """Membership lookup boundary so role checks hit the database, not the token."""

    async def role_for(self, tenant_id: UUID, subject: str) -> Role | None:
        """Return the subject's current role in the tenant, or None when absent."""
        ...


class RunCreationPort(Protocol):
    """Run creation boundary so idempotency and hashing stay out of the HTTP layer."""

    async def create(
        self,
        *,
        tenant_id: UUID,
        domain: str,
        depth: Literal["standard", "deep"],
        idempotency_key: str,
        request_hash: str,
    ) -> RunReceipt:
        """Create (or return the existing) run for this idempotency key."""
        ...


class StatePort(Protocol):
    """Run-state boundary so reads and cancels are tenant-scoped by construction."""

    async def get(self, tenant_id: UUID, run_id: UUID) -> RunReceipt | None:
        """Return the run's client-safe state, or None when not visible to the tenant."""
        ...

    async def cancel(self, tenant_id: UUID, run_id: UUID) -> RunReceipt | None:
        """Request cancellation and return the resulting state, or None when invisible."""
        ...


class TrackerViewPort(Protocol):
    """Tracker boundary exposing only the client-safe view, never internal rows."""

    async def client_view(self, tenant_id: UUID, run_id: UUID) -> dict[str, object] | None:
        """Return the client-safe tracker view, or None when not yet available."""
        ...


class DeliverablePort(Protocol):
    """Delivery boundary listing artifacts the tenant is allowed to see."""

    async def delivered_versions(
        self, tenant_id: UUID, *, run_id: UUID | None, account_id: UUID | None
    ) -> tuple[DeliveredVersion, ...]:
        """Return delivered versions scoped by run or account, newest last."""
        ...


class LinkSigner(Protocol):
    """Signing boundary so download URLs are bounded in time and bound to content."""

    async def create_link(self, artifact_id: UUID, sha256: str, expires_at: datetime) -> str:
        """Return a signed, expiring download URL for one artifact version."""
        ...


class DomainResolverPort(Protocol):
    """DNS boundary using allow-listed egress with one bounded lookup per validation."""

    async def resolve_public(self, domain: str) -> tuple[str, ...]:
        """Return the domain's resolved public addresses, or an empty tuple."""
        ...


class BlockedEntityPort(Protocol):
    """Blocklist boundary checked before any resolution or spend happens."""

    async def is_blocked(self, domain: str) -> bool:
        """Return whether the domain is on the blocked-entity policy list."""
        ...


class RateLimiterPort(Protocol):
    """Spend-aware limiter boundary so each endpoint declares its cost."""

    async def consume(
        self,
        tenant_id: UUID,
        principal_id: str,
        endpoint: str,
        cost: float,
    ) -> RateLimitDecision:
        """Charge one request's cost and return the allow/retry decision."""
        ...


class SecretPort(Protocol):
    """Secret boundary so key material never lives in code or configuration files."""

    async def key_pepper(self) -> bytes:
        """Return the pepper used when hashing tenant-scoped identifiers."""
        ...


class Clock(Protocol):
    """Time boundary so expiry and pacing logic stay deterministic in tests."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        ...

    def monotonic(self) -> float:
        """Return a monotonic reading for measuring elapsed time."""
        ...
