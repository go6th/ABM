"""Fake ports for testing."""

from __future__ import annotations
import datetime
from uuid import UUID
from account_intelligence.tracker.ports import CallLedgerPort, RunInputPort, ChecklistSource, Clock
from account_intelligence.tracker.model import CallRecord
from account_intelligence.tracker.config import ChecklistConfig, load_checklist_config

class FakeCallLedger(CallLedgerPort):
    def __init__(self):
        self.records: dict[str, CallRecord] = {}
        
    def get_call_record(self, tenant_id: UUID, run_id: UUID, call_ref: str) -> CallRecord:
        if call_ref not in self.records:
            raise KeyError(f"Unknown call_ref {call_ref}")
        return self.records[call_ref]

class FakeRunInput(RunInputPort):
    def __init__(self, scoped: bool = True):
        self.scoped = scoped
        
    def is_lane_in_scope(self, run_id: UUID, lane: str) -> bool:
        return self.scoped

class FakeChecklist(ChecklistSource):
    def get_checklist(self) -> ChecklistConfig:
        cfg, _ = load_checklist_config()
        return cfg

class FakeClock(Clock):
    def __init__(self, time: datetime.datetime):
        self.time = time
        
    def now(self) -> datetime.datetime:
        return self.time
