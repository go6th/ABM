"""Tracker ports (dependency injection)."""

from __future__ import annotations

import datetime
from typing import Protocol
from uuid import UUID

from account_intelligence.tracker.config import ChecklistConfig
from account_intelligence.tracker.model import CallRecord


class NotConfigured(Exception):
    pass


class CallLedgerPort(Protocol):
    def get_call_record(self, tenant_id: UUID, run_id: UUID, call_ref: str) -> CallRecord: ...


class RunInputPort(Protocol):
    def is_lane_in_scope(self, run_id: UUID, lane: str) -> bool: ...


class ChecklistSource(Protocol):
    def get_checklist(self) -> ChecklistConfig: ...


class Clock(Protocol):
    def now(self) -> datetime.datetime: ...
