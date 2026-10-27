"""Ports the grading layer depends on. Production adapters that do not exist raise
``NotConfigured``; the pipeline then never grants A, B or Confirmed on their behalf."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from account_intelligence.grading.config import Origin


@dataclass(frozen=True)
class RunInfo:
    """The run as grading sees it: tenant, account, start time, domain and primary hosts."""

    tenant_id: UUID
    account_id: UUID
    run_id: UUID
    started_at: datetime
    account_domain: str
    account_name: str | None = None
    # Verified careers host and other run-derived primary hosts (T1, same lineage group).
    primary_hosts: frozenset[str] = frozenset()
    # The entity lock's registrable domains (the account's own, the host its site redirects to,
    # subsidiaries): the company's own pages, so T1 like the account domain (2026-10-24).
    allowed_domains: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RunSource:
    """Stored text of one source record obtained in a run (via the connector ledger)."""

    source_ref: str  # neutral payload key plus record index
    call_ref: str  # ledger key of the fetch that produced it
    run_id: UUID
    url: str
    url_normalized: str
    host: str
    text: str
    origin: Origin
    provider_code: str
    operation: str
    fetched_at: datetime
    published_at: datetime | None = None
    lineage_hint: str | None = None  # upstream URL the provider states, if any


@dataclass(frozen=True)
class FetchRecord:
    """How and when a source was obtained in a run (G5)."""

    call_ref: str
    run_id: UUID
    provider_code: str
    operation: str
    origin: Origin
    tool_category: str
    fetched_at: datetime
    success: bool
    queries: tuple[str, ...] = ()  # what was actually asked (for the Lead rule)


class SourceTextPort(Protocol):
    """Stored text of this run's sources (the only way grading reads page text)."""

    async def sources(self, run: RunInfo) -> Sequence[RunSource]:
        """All stored source records of this run (never another run or tenant)."""
        ...


class FetchLedgerPort(Protocol):
    """How and when each source was obtained this run (G5, G9)."""

    async def fetches(self, run: RunInfo) -> Mapping[str, FetchRecord]:
        """Every ledgered call of this run keyed by call_ref, including failed calls."""
        ...


class SupportJudgePort(Protocol):
    """The judge: sees only a claim and a sentence; must not be the generator's model."""

    model_task: str

    async def judge(self, claim: str, sentence: str) -> Any:
        """Return raw judge output (validated by ``judge.JudgeVerdict``) or raise."""
        ...


class Clock(Protocol):
    """Injected time."""

    def now(self) -> datetime:
        """Current aware UTC time."""
        ...
