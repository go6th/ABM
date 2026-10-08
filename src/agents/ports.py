"""Typed boundaries of the agent layer.

Each port is satisfied by an existing component (one implementation per rule): provider calls go
through the connector ledger and credit gate, facts through the grading engine and
``record_graded_fact``, row statuses through the tracker, files through the export builders, the
gate through the quality gate and money through the wallet's single settlement. Tests use the
fakes in ``tests/agents/fakes.py``; a production adapter that does not exist raises
``NotConfigured`` (``agents.adapters``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel

from account_intelligence.platform.ports import Clock as Clock
from account_intelligence.platform.ports import NotConfigured as NotConfigured
from agents.manifest import PullManifest
from agents.schemas import FactCandidate, PayloadView, PersonCandidate, StoredFact
from spend.gate import RunCapState

M = TypeVar("M", bound=BaseModel)


@dataclass(frozen=True)
class RunContext:
    """Who and what a run is for. ``tenant_id`` comes from the verified token via the run row,
    never from a request body or model output."""

    tenant_id: UUID
    run_id: UUID
    account_key: str
    account_name: str
    domain: str
    attended: bool
    config_version: str


@dataclass(frozen=True)
class ProviderResult:
    """One typed provider call as the ledger recorded it. ``body`` goes to the payload store."""

    url: str
    body: str
    result_count: int
    cost_micros: int
    tool_category: str
    call_ref: str


class ProviderFailed(RuntimeError):
    """A provider call failed after the node RetryPolicy; ``reason`` becomes the Blocked reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class CapPause(RuntimeError):
    """The credit gate refused a call (no cap, cap reached, unknown cost): pause or record."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ProviderPort(Protocol):
    """Typed provider operations through the ledger (pull once, credit gate, routed order)."""

    async def call(self, ctx: RunContext, need: str, query: str) -> ProviderResult:
        """One routed call; replays are free (pull once). Raises CapPause or ProviderFailed."""
        ...


class PayloadPort(Protocol):
    """Raw payloads under neutral keys; read back as quarantined views."""

    async def put(self, ctx: RunContext, row_key: str, url: str, body: str, visibility: str) -> str:
        """Store one payload; the neutral reference."""
        ...

    async def view(self, tenant_id: UUID, ref: str) -> PayloadView:
        """The stored payload (raises KeyError when missing for this tenant)."""
        ...


class ManifestStore(Protocol):
    """Pull manifests (insert-only)."""

    async def save(self, manifest: PullManifest) -> None:
        """Store a manifest once per run (idempotent)."""
        ...

    async def for_run(self, tenant_id: UUID, run_id: UUID) -> PullManifest | None:
        """The manifest a given pull run produced."""
        ...

    async def latest(self, tenant_id: UUID, account_key: str) -> PullManifest | None:
        """The newest manifest for the account (offered for a Phase 2 only run)."""
        ...


@dataclass(frozen=True)
class ConsentRecord:
    """D-97: a tenant-level people data consent."""

    tenant_id: UUID
    granted_by: str
    granted_at: datetime
    text_version: str
    scopes: frozenset[str]
    revoked_at: datetime | None = None


class ConsentStore(Protocol):
    """Consent records (insert-only; revocation is a new row)."""

    async def current(self, tenant_id: UUID, scope: str) -> ConsentRecord | None:
        """The newest record for the scope, or None."""
        ...


@dataclass(frozen=True)
class InternalHistorySettings:
    """D-98 switches, as the tenant admin set them through the Phase 3 connectors."""

    enabled: bool = False
    admin_consent: bool = False
    named_folders: tuple[str, ...] = ()
    connector_connected: bool = False


@dataclass(frozen=True)
class TenantAgentSettings:
    """Per-client settings that replace operator choices (work item 4)."""

    pursuing_client: str | None
    lanes: tuple[str, ...]
    depth: str = "standard"
    internal_history: InternalHistorySettings = field(default_factory=InternalHistorySettings)


class SettingsPort(Protocol):
    """Tenant settings."""

    async def get(self, tenant_id: UUID) -> TenantAgentSettings:
        """The tenant's agent settings."""
        ...


@dataclass(frozen=True)
class GradingCandidate:
    """A candidate handed to the grading engine with what the agent layer verified."""

    candidate: FactCandidate
    urls: tuple[str, ...]
    visibility: str
    read_this_run: bool


@dataclass(frozen=True)
class GradeBatch:
    """The engine's verdicts: stored facts (``item_index[i]`` is the input item of ``facts[i]``)
    and counted drops."""

    facts: tuple[StoredFact, ...]
    item_index: tuple[int, ...]
    drops: Mapping[str, int]


class GradingPort(Protocol):
    """The grading engine and ``record_graded_fact`` (grades and promotion live only there)."""

    async def grade(self, ctx: RunContext, items: Sequence[GradingCandidate]) -> GradeBatch:
        """Grade and record; every item ends as a fact or a counted drop."""
        ...

    async def mark_conflicting(
        self, ctx: RunContext, fact_ids: Sequence[str], reason: str
    ) -> tuple[str, ...]:
        """Supersede the facts as Conflicting (new rows, never overwritten)."""
        ...

    async def facts(self, ctx: RunContext) -> tuple[StoredFact, ...]:
        """The run's current (not superseded) facts."""
        ...

    async def record_people(
        self, ctx: RunContext, people: Sequence[PersonCandidate]
    ) -> tuple[str, ...]:
        """Record people through the people pipeline; their ids."""
        ...


@dataclass(frozen=True)
class RowOutcome:
    """A coverage row as the tracker holds it."""

    status: str | None
    reason: str | None = None
    queries: int = 0


class TrackerPort(Protocol):
    """The tracker: statuses are derived there from ledger evidence, never typed here."""

    async def report(
        self, ctx: RunContext, row_key: str, call_refs: Sequence[str], reason: str | None
    ) -> RowOutcome:
        """Report a row's evidence (or not-attempted reason); the derived outcome."""
        ...

    async def rows(self, ctx: RunContext) -> Mapping[str, RowOutcome]:
        """Every row of the run."""
        ...

    async def mark_partial(self, ctx: RunContext, row_key: str, reason: str) -> RowOutcome:
        """Downgrade a row to Partial with a reason (the tracker's downgrade path)."""
        ...


class CreditPort(Protocol):
    """The credit manager: cap state, prices and approval requests (D-96)."""

    async def cap_state(self, ctx: RunContext) -> RunCapState:
        """The run's cap enforcement columns."""
        ...

    async def price(self, need: str) -> tuple[int, bool]:
        """``(estimate_micros, cost_known)`` for one call of a route need."""
        ...

    async def request_approval(
        self, ctx: RunContext, proposals: Sequence[Mapping[str, Any]], total_micros: int
    ) -> str:
        """Open an approval request (the cap-pause path); its id."""
        ...


@dataclass(frozen=True)
class SenseCheckRecord:
    """A stored sense-check result bound to its inputs (G6)."""

    run_id: UUID
    manifest_digest: str
    facts_digest: str
    findings: int
    proposals: int


class RunRecordStore(Protocol):
    """Run-scoped records the agent layer owns: sense checks and the mode lineage."""

    async def save_sense_check(self, ctx: RunContext, record: SenseCheckRecord) -> None:
        """Store the sense-check result (idempotent per run and inputs)."""
        ...

    async def sense_check(self, ctx: RunContext) -> SenseCheckRecord | None:
        """The run's latest sense-check result."""
        ...

    async def save_distill(self, ctx: RunContext, audience: str, output: BaseModel) -> None:
        """Store a distill output for the builders (idempotent per run and audience)."""
        ...


class InternalSourcePort(Protocol):
    """D-98 internal history through the Phase 3 connectors (quarantined, access logged)."""

    async def search(self, ctx: RunContext, row_key: str, query: str) -> ProviderResult:
        """Search the tenant's named folders; content is private (Internal Only)."""
        ...


class AgentModel(Protocol):
    """A routed model call returning a validated output (``llm.port.Router`` in production)."""

    async def call(
        self,
        task_kind: str,
        prompt_text: str,
        prompt_hash: str,
        payload: Mapping[str, Any],
        output: type[M],
        ctx: RunContext,
    ) -> tuple[M, int]:
        """The validated output and the tokens used."""
        ...


class BuilderPort(Protocol):
    """Export builders (step 6, code only)."""

    async def build(self, ctx: RunContext, audience: str) -> tuple[str, ...]:
        """Build the audience's file set; the stored file ids."""
        ...

    async def texts(self, ctx: RunContext, file_id: str) -> tuple[str, ...]:
        """Every text run of a built file (for the client-output scan, G9)."""
        ...


class GatePort(Protocol):
    """The pre-delivery quality gate (step 7, code only)."""

    async def check(self, ctx: RunContext) -> Mapping[str, list[str]]:
        """Failing checks by name (empty when the gate passes)."""
        ...


class DeliveryPort(Protocol):
    """Release (step 8, code only). Never sends outreach."""

    async def release(self, ctx: RunContext, file_ids: Sequence[str]) -> None:
        """Publish the files to the tenant's workspace."""
        ...


class WalletPort(Protocol):
    """The single settlement per run."""

    async def settle_once(self, ctx: RunContext, platform_fault: bool) -> bool:
        """Settle once; False when already settled."""
        ...


@dataclass(frozen=True)
class RunRecord:
    """A run row as the agent layer needs it (mode and lineage recorded on every run, G17)."""

    tenant_id: UUID
    run_id: UUID
    mode: str
    parent_run_id: UUID | None
    account_key: str
    account_name: str
    domain: str
    config_version: str
    config_version_reason: str
    attended: bool
    cap_request_micros: int
    acknowledged_stale: tuple[str, ...] = ()


class RunStore(Protocol):
    """Run rows (the existing ``account_runs`` table in production)."""

    async def create(self, record: RunRecord, idempotency_key: str) -> tuple[RunRecord, bool]:
        """Insert once per ``(tenant, idempotency_key)``; the stored row and whether it is new."""
        ...

    async def get(self, tenant_id: UUID, run_id: UUID) -> RunRecord | None:
        """A run of this tenant (RLS: another tenant's run is None)."""
        ...
