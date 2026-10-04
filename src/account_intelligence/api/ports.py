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
    subject: str
    tenant_id: UUID
    role: Role
    principal_type: Literal["jwt", "api_key", "operator_support"] = "jwt"


@dataclass(frozen=True)
class RunReceipt:
    run_id: UUID
    status: str
    created_at: datetime


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int


@dataclass(frozen=True)
class DeliveredVersion:
    artifact_id: UUID
    name: str
    kind: str
    size_bytes: int
    sha256: str
    version: int
    gate_status: str
    delivered_at: datetime


class IdentityVerifier(Protocol):
    async def verify(self, credential: str) -> Principal: ...


class MembershipPort(Protocol):
    async def role_for(self, tenant_id: UUID, subject: str) -> Role | None: ...


class RunCreationPort(Protocol):
    async def create(
        self,
        *,
        tenant_id: UUID,
        domain: str,
        depth: Literal["standard", "deep"],
        idempotency_key: str,
        request_hash: str,
    ) -> RunReceipt: ...


class StatePort(Protocol):
    async def get(self, tenant_id: UUID, run_id: UUID) -> RunReceipt | None: ...

    async def cancel(self, tenant_id: UUID, run_id: UUID) -> RunReceipt | None: ...


class TrackerViewPort(Protocol):
    async def client_view(self, tenant_id: UUID, run_id: UUID) -> dict[str, object] | None: ...


class DeliverablePort(Protocol):
    async def delivered_versions(
        self, tenant_id: UUID, *, run_id: UUID | None, account_id: UUID | None
    ) -> tuple[DeliveredVersion, ...]: ...


class LinkSigner(Protocol):
    async def create_link(self, artifact_id: UUID, sha256: str, expires_at: datetime) -> str: ...


class DomainResolverPort(Protocol):
    async def resolve_public(self, domain: str) -> tuple[str, ...]: ...


class BlockedEntityPort(Protocol):
    async def is_blocked(self, domain: str) -> bool: ...


class RateLimiterPort(Protocol):
    async def consume(
        self,
        tenant_id: UUID,
        principal_id: str,
        endpoint: str,
        cost: float,
    ) -> RateLimitDecision: ...


class SecretPort(Protocol):
    async def key_pepper(self) -> bytes: ...


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...
