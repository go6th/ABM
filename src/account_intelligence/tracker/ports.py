"""Tracker ports. Production adapters live in ``tracker/adapters.py``; fakes in ``tests/fakes``."""

from __future__ import annotations

import datetime
from typing import Protocol
from uuid import UUID

from account_intelligence.platform.ports import NotConfigured as NotConfigured  # F-6
from account_intelligence.tracker.config import ChecklistConfig
from account_intelligence.tracker.model import CallRecord


class CallLedgerPort(Protocol):
    """Read-only: resolve a call reference of THIS tenant and run to its ledger record (T2).

    Raises ``KeyError`` when the reference is not a call of this run (another run, another
    tenant, a previous run, or made up)."""

    async def get_call_record(self, tenant_id: UUID, run_id: UUID, call_ref: str) -> CallRecord:
        """The ledger record of one call of this tenant and run (KeyError otherwise)."""
        ...


class RunInputPort(Protocol):
    """The run's scope as given at creation (T4) and names that must not leak (T9)."""

    async def is_lane_in_scope(self, tenant_id: UUID, run_id: UUID, lane: str) -> bool:
        """Whether the run input leaves the lane switched on."""
        ...

    async def other_account_names(self, tenant_id: UUID, run_id: UUID) -> tuple[str, ...]:
        """The tenant's other accounts' names (scrubbed from detail, T9)."""
        ...


class ChecklistSource(Protocol):
    """Where the 33 rows come from (the validated checklist)."""

    def get_checklist(self) -> ChecklistConfig:
        """The validated checklist."""
        ...


class Clock(Protocol):
    """Injected time; pure modules never read the clock."""

    def now(self) -> datetime.datetime:
        """Current aware UTC time."""
        ...
