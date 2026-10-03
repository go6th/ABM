"""Everything one run's stages need, built by ``app.container`` and passed to the handlers.

Identity (tenant, run, account) is taken from the run row loaded under the verified tenant, never
from a request body, a tool argument or model output (W3). Stages receive only this object and
the small checkpoint state.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.connectors.base import CallContext
from account_intelligence.grading.config import load_grading_config
from account_intelligence.platform.ports import Clock, LlmPort, PayloadStore
from account_intelligence.tracker.adapters import LedgerCallAdapter, RunInputAdapter
from account_intelligence.tracker.repo import CoverageRepo
from account_intelligence.tracker.service import TrackerService
from account_intelligence.workflow.spec import WorkflowSpec

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class RunDeps:
    """One run's dependencies. ``connectors`` is ``None`` when no provider is configured: every
    research row then ends Not done with ``not_available_yet`` (never a fake success)."""

    tenant_id: UUID
    run_id: UUID
    account_id: UUID
    domain: str
    txn: TxnFactory
    llm: LlmPort
    payloads: PayloadStore
    clock: Clock
    spec: WorkflowSpec
    connectors: Any | None = None
    builder: Any | None = None
    gate: Any | None = None
    judge: Any | None = None
    canary: str = "CANARY-7f3a9e"
    retry_max_attempts: int = 3
    extra_allowed_hosts: frozenset[str] = field(default_factory=frozenset)
    run_input: dict[str, Any] = field(default_factory=dict)
    deadline: float | None = None

    def call_context(self) -> CallContext:
        """Connector call identity, from the run (never from arguments)."""
        return CallContext(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            account_id=self.account_id,
            account_domain=self.domain,
            extra_allowed_hosts=self.extra_allowed_hosts,
            deadline=self.deadline,
        )

    def tracker(self, session: AsyncSession) -> TrackerService:
        """The tracker on one tenant transaction, wired to the connector ledger."""
        return TrackerService(
            CoverageRepo(session),
            LedgerCallAdapter(session, load_grading_config().category_by_operation),
            RunInputAdapter(session),
            owners=self.spec.coverage_owners,
        )

    def payload_key(self, kind: str, name: str) -> str:
        """Neutral key under this run (no provider name, no account name)."""
        return (
            f"tenants/{self.tenant_id}/accounts/{self.account_id}/runs/{self.run_id}/{kind}/{name}"
        )
