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
    # the entity lock (master prompt v2 section 3): the registrable domains that belong to the
    # account (its own, the host its site redirects to, subsidiaries) and the look-alike hosts
    # that never do; empty until the graph's entity_lock node ran
    allowed_domains: tuple[str, ...] = ()
    blocked_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceItem:
    """One source record a call returned: its link, its text and, when code knows it (a feed's
    date range), the match kind. One item is one payload the extractors may quote."""

    url: str
    text: str
    match_kind: str | None = None  # "keyword_match" | "date_range" | None
    # what the record behind the item states about itself, as (name, text) pairs: its kind
    # (``news``, ``page``), title, summary, published date, category. The link archive builds
    # the structured copy of the address from them; the extractors read only ``text``.
    meta: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ProviderResult:
    """One routed call as the ledger recorded it: its source items, counts, cost and the ledger
    references the tracker reads its proof from."""

    items: tuple[SourceItem, ...]
    result_count: int
    cost_micros: int
    tool_category: str
    call_refs: tuple[str, ...]


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

    async def call(self, ctx: RunContext, need: str, query: str, row_key: str) -> ProviderResult:
        """One routed call for a coverage row; replays are free (pull once). Raises CapPause or
        ProviderFailed."""
        ...

    async def entity_lock(self, ctx: RunContext) -> Any:
        """The entity lock built from the account's own home page and one brand search (an
        ``agents.entity.EntityLock``): run before any other pull. Raises CapPause."""
        ...


@dataclass(frozen=True)
class PipelineOutcome:
    """What the code pipelines did after the pulls: counts only (never text)."""

    counters: Mapping[str, int] = field(default_factory=dict)
    exits: Mapping[str, str] = field(default_factory=dict)
    # the ledger keys of the calls the pipeline made for a row it owns, when the graph must report
    # that row for it (the technology detections handed over at the pull ceiling)
    calls: tuple[str, ...] = ()


class PipelinePort(Protocol):
    """The skill's code-run steps after Phase 1: the job pipeline (step 6: every posting of the
    last 12 months read, classified and counted; templated claims graded) and the technology
    detections written as dated rows (step 7). Both pull once (ledger replay) and report their
    coverage rows to the tracker themselves."""

    async def run_jobs(self, ctx: RunContext, careers_url: str | None) -> PipelineOutcome:
        """Run the job pipeline for the run (a replay makes no paid call)."""
        ...

    async def write_detections(self, ctx: RunContext) -> PipelineOutcome:
        """Write the run's technology detections as rows (insert-only, idempotent)."""
        ...

    async def write_stack(self, ctx: RunContext) -> PipelineOutcome:
        """Write the organisation's tech stack as rows (insert-only, idempotent; both lookups
        were made in Phase 1 and replay free)."""
        ...

    async def run_people(self, ctx: RunContext) -> PipelineOutcome:
        """Run the people pipeline for the run (the caller has checked consent, G8)."""
        ...


class PayloadPort(Protocol):
    """Raw payloads under neutral keys; read back as quarantined views."""

    async def put(self, ctx: RunContext, row_key: str, item: SourceItem, visibility: str) -> str:
        """Store one source item; the neutral reference."""
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
    urls: tuple[str, ...]  # one per evidence item, in order
    visibility: str
    read_this_run: bool


@dataclass(frozen=True)
class GradeBatch:
    """The engine's verdicts: stored facts (``item_index[i]`` is the input item of ``facts[i]``)
    and counted drops."""

    facts: tuple[StoredFact, ...]
    item_index: tuple[int, ...]
    drops: Mapping[str, int]
    # The engine's own "read in a primary source this run" per fact (empty: use the item's hint)
    read_in_primary: tuple[bool, ...] = ()


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

    async def propose_cap(self, ctx: RunContext) -> bool:
        """Propose the run's cap from the pull plan; the tenant policy may auto-approve it within
        the created budget. True when an approved cap exists afterwards."""
        ...

    async def price(self, need: str) -> tuple[int, bool]:
        """``(estimate_micros, cost_known)`` for one call of a route need."""
        ...

    async def request_approval(
        self, ctx: RunContext, proposals: Sequence[Mapping[str, Any]], total_micros: int
    ) -> str:
        """Open an approval request (the cap-pause path); its id."""
        ...

    async def accept_tokens(self, ctx: RunContext) -> None:
        """The user continued the run past its token guard: the next pause is the guard's
        multiple of the estimate on top of the tokens used now."""
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

    async def save_entity_lock(self, ctx: RunContext, lock: Mapping[str, Any]) -> None:
        """Store the run's entity lock on the account (the latest research wins)."""
        ...

    async def entity_lock(self, ctx: RunContext) -> Mapping[str, Any] | None:
        """The account's stored entity lock, if a research locked it."""
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

    async def release(
        self, ctx: RunContext, file_ids: Sequence[str], withheld: Sequence[str] = ()
    ) -> None:
        """Publish the files to the tenant's workspace; ``withheld`` names the client file kinds
        the gate held back, which the delivery records (they are not published)."""
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
