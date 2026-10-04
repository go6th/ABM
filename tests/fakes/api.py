"""In-memory API service fakes for hermetic contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from account_intelligence.api.errors import APIProblem
from account_intelligence.api.ports import (
    DeliveredVersion,
    Principal,
    RateLimitDecision,
    RunReceipt,
)


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 1, 1, tzinfo=UTC)
        self._monotonic = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds


class FakeIdentityVerifier:
    def __init__(self, principals: dict[str, Principal]) -> None:
        self.principals = principals

    async def verify(self, credential: str) -> Principal:
        if credential not in self.principals:
            raise APIProblem(401, "invalid_credential", "Unauthorized", "Invalid credential.")
        return self.principals[credential]


class FakeMembershipPort:
    def __init__(self) -> None:
        self.roles: dict[tuple[UUID, str], str] = {}

    async def role_for(self, tenant_id: UUID, subject: str) -> str | None:
        return self.roles.get((tenant_id, subject))


class FakeRunCreationPort:
    def __init__(self) -> None:
        self.responses: dict[tuple[UUID, str], tuple[str, RunReceipt]] = {}

    async def create(
        self,
        *,
        tenant_id: UUID,
        domain: str,
        depth: Literal["standard", "deep"],
        idempotency_key: str,
        request_hash: str,
    ) -> RunReceipt:
        key = (tenant_id, idempotency_key)
        previous = self.responses.get(key)
        if previous is not None:
            if previous[0] != request_hash:
                raise APIProblem(
                    409,
                    "idempotency_key_reuse",
                    "Idempotency key conflict",
                    "The idempotency key was already used for a different request.",
                )
            return previous[1]
        receipt = RunReceipt(uuid4(), "QUEUED", datetime.now(UTC))
        self.responses[key] = (request_hash, receipt)
        return receipt


class FakeStatePort:
    def __init__(self) -> None:
        self.runs: dict[tuple[UUID, UUID], RunReceipt] = {}

    async def get(self, tenant_id: UUID, run_id: UUID) -> RunReceipt | None:
        return self.runs.get((tenant_id, run_id))

    async def cancel(self, tenant_id: UUID, run_id: UUID) -> RunReceipt | None:
        return self.runs.get((tenant_id, run_id))


class FakeTrackerViewPort:
    async def client_view(self, tenant_id: UUID, run_id: UUID) -> dict[str, object] | None:
        return {"rows": []}


class FakeDeliverablePort:
    def __init__(self, versions: tuple[DeliveredVersion, ...] = ()) -> None:
        self.versions = versions

    async def delivered_versions(
        self, tenant_id: UUID, *, run_id: UUID | None, account_id: UUID | None
    ) -> tuple[DeliveredVersion, ...]:
        return self.versions


class FakeLinkSigner:
    async def create_link(self, artifact_id: UUID, sha256: str, expires_at: datetime) -> str:
        return f"https://synthetic.invalid/{artifact_id}/{sha256}"


class FakeDomainResolver:
    def __init__(self, addresses: tuple[str, ...] = ("8.8.8.8",)) -> None:
        self.addresses = addresses
        self.calls = 0

    async def resolve_public(self, domain: str) -> tuple[str, ...]:
        self.calls += 1
        return self.addresses


class FakeBlockedEntityPort:
    def __init__(self, blocked: set[str] | None = None) -> None:
        self.blocked = blocked or set()

    async def is_blocked(self, domain: str) -> bool:
        return domain in self.blocked


class FakeRateLimiter:
    def __init__(self, allowed: bool = True, retry_after_seconds: int = 0) -> None:
        self.decision = RateLimitDecision(allowed, retry_after_seconds)

    async def consume(
        self,
        tenant_id: UUID,
        principal_id: str,
        endpoint: str,
        cost: float,
    ) -> RateLimitDecision:
        return self.decision


class FakeSecretPort:
    async def key_pepper(self) -> bytes:
        return b"synthetic-key-pepper"
