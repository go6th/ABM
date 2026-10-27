"""Harness for the ABM agent tests: real graph, real guards and config, scripted fakes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agents.config import SkillConfig, load_skill_config
from agents.contracts import load_registry
from agents.graph import AbmDeps
from agents.ports import ConsentRecord, TenantAgentSettings
from agents.schemas import RunRequest
from agents.service import AbmRunService
from tests.agents import worlds
from tests.agents.fakes import (
    FREE_NEEDS,
    FakeBuilder,
    FakeClock,
    FakeConsent,
    FakeCredit,
    FakeDelivery,
    FakeGate,
    FakeGrading,
    FakeInternal,
    FakeLlm,
    FakeManifests,
    FakePayloads,
    FakePipelines,
    FakeRecords,
    FakeRuns,
    FakeSettings,
    FakeTracker,
    FakeWallet,
    StubLedgerProvider,
    World,
    honest_llm,
)

TEXT_VERSION = "test-consent-v1"


def consent_cfg(cfg: SkillConfig, version: str | None = TEXT_VERSION) -> SkillConfig:
    """The config with an approved consent text version (the real one is TODO, D-97)."""
    from dataclasses import replace

    return replace(cfg, consent_text_version=version)


@dataclass
class Harness:
    """One stub world wired to a service."""

    world: World
    cfg: SkillConfig
    tenant: UUID = field(default_factory=uuid4)
    clock: FakeClock = field(default_factory=FakeClock)
    credit: FakeCredit = field(default_factory=FakeCredit)
    payloads: FakePayloads = field(default_factory=FakePayloads)
    manifests: FakeManifests = field(default_factory=FakeManifests)
    consent: FakeConsent = field(default_factory=FakeConsent)
    settings: FakeSettings = field(default_factory=FakeSettings)
    grading: FakeGrading = field(default_factory=FakeGrading)
    tracker: FakeTracker = field(default_factory=FakeTracker)
    records: FakeRecords = field(default_factory=FakeRecords)
    gate: FakeGate = field(default_factory=FakeGate)
    delivery: FakeDelivery = field(default_factory=FakeDelivery)
    wallet: FakeWallet = field(default_factory=FakeWallet)
    runs: FakeRuns = field(default_factory=FakeRuns)
    internal: FakeInternal | None = None
    llm: FakeLlm | None = None

    def __post_init__(self) -> None:
        self.provider = StubLedgerProvider(self.world, self.cfg, self.credit)
        self.pipelines = FakePipelines(self.world, self.cfg, self.grading, self.tracker)
        self.builder = FakeBuilder(self.grading, self.records)
        self.llm = self.llm or honest_llm(self.cfg, self.world)
        self.saver = InMemorySaver()
        self.service = self._service()

    def _service(self) -> AbmRunService:
        assert self.llm is not None
        deps = AbmDeps(
            cfg=self.cfg,
            registry=load_registry(),
            clock=self.clock,
            model=self.llm,
            provider=self.provider,
            payloads=self.payloads,
            manifests=self.manifests,
            consent=self.consent,
            settings=self.settings,
            grading=self.grading,
            tracker=self.tracker,
            credit=self.credit,
            records=self.records,
            builder=self.builder,
            gate=self.gate,
            delivery=self.delivery,
            wallet=self.wallet,
            internal=self.internal,
            free_operations=FREE_NEEDS,
            pipelines=self.pipelines,
        )
        return AbmRunService(deps, self.runs, self.saver)

    def rebuild(self) -> None:
        """A new process on the same stores and checkpoints (after a crash)."""
        self.service = self._service()

    def grant_consent(self, version: str = TEXT_VERSION) -> None:
        self.consent.record = ConsentRecord(
            tenant_id=self.tenant,
            granted_by="admin-synthetic",
            granted_at=self.clock.now() - timedelta(days=1),
            text_version=version,
            scopes=frozenset({"people_data"}),
        )

    def request(self, **kw: object) -> RunRequest:
        return RunRequest(account_name=self.world.name, domain=self.world.domain, **kw)  # type: ignore[arg-type]

    async def start(
        self, key: str, *, attended: bool = False, budget: int | None = 10_000_000, **kw: object
    ):
        record = await self.service.start(
            self.tenant, self.request(**kw), key, attended=attended, cap_request_micros=budget or 0
        )
        if budget is not None:
            self.credit.approve(record.run_id, budget)
        return record

    async def run(self, key: str, **kw: object):
        record = await self.start(key, **kw)
        return record, await self.service.execute(self.tenant, record.run_id)


@pytest.fixture
def cfg() -> SkillConfig:
    return consent_cfg(load_skill_config())


@pytest.fixture
def make(cfg: SkillConfig) -> Callable[..., Harness]:
    def build(world: World | None = None, **kw: object) -> Harness:
        return Harness(world or worlds.rich(), kw.pop("cfg", cfg), **kw)  # type: ignore[arg-type]

    return build


@pytest.fixture
def h(make: Callable[..., Harness]) -> Harness:
    harness = make()
    harness.settings.settings = TenantAgentSettings(
        pursuing_client="Synthetic Partner Co", lanes=("salesforce", "data_ai")
    )
    return harness
