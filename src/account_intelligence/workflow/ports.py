"""Typed workflow boundaries; missing production integrations fail closed."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class NotConfigured(RuntimeError):
    """A required production adapter has not been implemented."""


class Clock(Protocol):
    def now(self) -> datetime: ...


class ConnectorPort(Protocol):
    async def call_operation(
        self, category: str, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]: ...
    def retry_policy_for(self, operation: str) -> Any: ...


class LlmPort(Protocol):
    async def generate(
        self, tier: str, prompt_id: str, input_data: dict[str, Any], output_schema: type[Any]
    ) -> Any: ...


class SourceRepo(Protocol):
    async def list_for_run(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]: ...


class FactRepo(Protocol):
    async def write_with_evidence(
        self, tenant_id: str, run_id: str, fact: dict[str, Any]
    ) -> str: ...
    async def list_for_run(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]: ...


class PeopleRepo(Protocol):
    async def upsert_many(
        self, tenant_id: str, run_id: str, people: list[dict[str, Any]]
    ) -> None: ...


class PostingRepo(Protocol):
    async def upsert_many(
        self, tenant_id: str, run_id: str, postings: list[dict[str, Any]]
    ) -> None: ...


class DetectionRepo(Protocol):
    async def upsert_many(
        self, tenant_id: str, run_id: str, detections: list[dict[str, Any]]
    ) -> None: ...


class CoverageRepo(Protocol):
    async def write_status(
        self, tenant_id: str, run_id: str, row: str, status: str, proof: dict[str, Any]
    ) -> None: ...


class PayloadStore(Protocol):
    async def get(self, tenant_id: str, run_id: str, reference: str) -> dict[str, Any] | None: ...
    async def put(
        self, tenant_id: str, run_id: str, reference: str, value: dict[str, Any]
    ) -> None: ...


class BuilderPort(Protocol):
    async def build(self, tenant_id: str, run_id: str) -> list[str]: ...


class GatePort(Protocol):
    async def check(self, tenant_id: str, run_id: str) -> dict[str, list[str]]: ...


class WalletPort(Protocol):
    async def settle_once(
        self, tenant_id: str, run_id: str, actual_cost_usd: float, platform_fault: bool
    ) -> bool: ...
