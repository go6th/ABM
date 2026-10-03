"""Typed workflow boundaries; missing production integrations fail closed."""

from __future__ import annotations

from typing import Any, Protocol

# One definition of each (audit F-6); re-exported for existing imports.
from account_intelligence.platform.ports import Clock as Clock
from account_intelligence.platform.ports import LlmPort as LlmPort
from account_intelligence.platform.ports import NotConfigured as NotConfigured
from account_intelligence.platform.ports import PayloadStore as PayloadStore


class ConnectorPort(Protocol):
    """Workflow spec port: provider calls (implemented by connectors.call_operation)."""

    async def call_operation(
        self, category: str, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Run one typed operation."""
        ...

    def retry_policy_for(self, operation: str) -> Any:
        """The node RetryPolicy for an operation."""
        ...


class SourceRepo(Protocol):
    """Workflow spec port: the run's sources."""

    async def list_for_run(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        """The run's sources."""
        ...


class FactRepo(Protocol):
    """Workflow spec port: facts with evidence (implemented by grading.GradingFactRepo)."""

    async def write_with_evidence(self, tenant_id: str, run_id: str, fact: dict[str, Any]) -> str:
        """Persist one graded fact with its evidence."""
        ...

    async def list_for_run(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        """The run's facts."""
        ...


class PeopleRepo(Protocol):
    """Workflow spec port: people rows (table exists; adapter is open work, p_people blocked)."""

    async def upsert_many(self, tenant_id: str, run_id: str, people: list[dict[str, Any]]) -> None:
        """Insert the run's people."""
        ...


class PostingRepo(Protocol):
    """Workflow spec port: postings (implemented by jobs.repo.PgPostingRepo)."""

    async def upsert_many(
        self, tenant_id: str, run_id: str, postings: list[dict[str, Any]]
    ) -> None:
        """Insert the run's postings."""
        ...


class DetectionRepo(Protocol):
    """Workflow spec port: technology detections (table exists; adapter is open work)."""

    async def upsert_many(
        self, tenant_id: str, run_id: str, detections: list[dict[str, Any]]
    ) -> None:
        """Insert the run's detections."""
        ...


class CoverageRepo(Protocol):
    """Workflow spec port: coverage rows (implemented by the tracker; statuses are derived, never
    typed)."""

    async def write_status(
        self, tenant_id: str, run_id: str, row: str, status: str, proof: dict[str, Any]
    ) -> None:
        """Record a row's outcome."""
        ...


class BuilderPort(Protocol):
    """Workflow spec port: deliverable builders (no production builder yet; NotConfigured)."""

    async def build(self, tenant_id: str, run_id: str) -> list[str]:
        """Generate the standing file set from stored rows."""
        ...


class GatePort(Protocol):
    """Workflow spec port: the quality gate (no production gate yet)."""

    async def check(self, tenant_id: str, run_id: str) -> dict[str, list[str]]:
        """Failing checks by name."""
        ...


class WalletPort(Protocol):
    """Workflow spec port: settlement (implemented by runs.lifecycle.settle_once)."""

    async def settle_once(
        self, tenant_id: str, run_id: str, actual_cost_usd: float, platform_fault: bool
    ) -> bool:
        """Settle the run exactly once."""
        ...
