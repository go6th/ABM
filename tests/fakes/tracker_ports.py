"""In-memory fakes for the tracker ports (scripted ledger, run input, clock)."""

from __future__ import annotations

import datetime
from uuid import UUID

from account_intelligence.tracker.config import ChecklistConfig, load_checklist
from account_intelligence.tracker.model import CallRecord


class FakeCallLedger:
    """Ledger records keyed by (tenant, run, ref): another run or tenant does not resolve."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str, str], CallRecord] = {}

    def add(self, tenant: UUID, run: UUID, record: CallRecord) -> str:
        self.records[(str(tenant), str(run), record.call_ref)] = record
        return record.call_ref

    async def get_call_record(self, tenant_id: UUID, run_id: UUID, call_ref: str) -> CallRecord:
        key = (str(tenant_id), str(run_id), call_ref)
        if key not in self.records:
            raise KeyError(call_ref)
        return self.records[key]


class FakeRunInput:
    def __init__(self, lanes_off: tuple[str, ...] = (), others: tuple[str, ...] = ()) -> None:
        self.lanes_off = lanes_off
        self.others = others

    async def is_lane_in_scope(self, tenant_id: UUID, run_id: UUID, lane: str) -> bool:
        return lane not in self.lanes_off

    async def other_account_names(self, tenant_id: UUID, run_id: UUID) -> tuple[str, ...]:
        return self.others


class FakeChecklist:
    def __init__(self, cfg: ChecklistConfig | None = None) -> None:
        self.cfg = cfg or load_checklist()

    def get_checklist(self) -> ChecklistConfig:
        return self.cfg


class FakeClock:
    def __init__(self, time: datetime.datetime):
        self.time = time

    def now(self) -> datetime.datetime:
        return self.time


class InMemoryCoverageRepo:
    """Same interface as ``tracker.repo.CoverageRepo``, in memory (stateful model tests only;
    the real repository is covered on Postgres by ``tests/tracker/test_service_db.py``)."""

    def __init__(self) -> None:
        from account_intelligence.tracker.errors import UnknownRow, VersionConflict

        self.UnknownRow, self.VersionConflict = UnknownRow, VersionConflict
        self.meta: dict[object, dict[str, object]] = {}
        self.rows: dict[tuple[object, str], dict[str, object]] = {}
        self.event_log: list[dict[str, object]] = []

    async def seed(self, run_id, tenant_id, rows, version, digest, policy, owners):  # type: ignore[no-untyped-def]
        if run_id in self.meta:
            return False
        self.meta[run_id] = {
            "checklist_version": version,
            "checklist_hash": digest,
            "pinned_policy": dict(policy),
            "seeded_row_count": len(rows),
        }
        for r in rows:
            self.rows[(run_id, r.key)] = {
                "priority": r.priority,
                "status": None,
                "reason": None,
                "proof": None,
                "version": 0,
                "attempts": 0,
                "swept": False,
                "owner": owners.get(r.key),
                "detail": None,
            }
        return True

    async def get_row(self, run_id, key):  # type: ignore[no-untyped-def]
        from account_intelligence.tracker.gap import TrackerRowState
        from account_intelligence.tracker.repo import StoredRow

        if (run_id, key) not in self.rows:
            raise self.UnknownRow(key)
        r = self.rows[(run_id, key)]
        proof = r["proof"]
        state = TrackerRowState(
            key=key,
            priority=r["priority"],
            status=r["status"],
            reason_code=r["reason"],
            proof_hash=proof.proof_hash if proof else None,
            swept=r["swept"],
            proof_calls=len(proof.call_refs) if proof else 0,
            attempts=r["attempts"],
        )
        return StoredRow(state, r["version"], proof, r["owner"], r["detail"])

    async def get_all_rows(self, run_id):  # type: ignore[no-untyped-def]
        return [
            (await self.get_row(run_id, key)).state
            for (run, key) in sorted(self.rows, key=lambda k: k[1])
            if run == run_id
        ]

    async def get_proofs(self, run_id):  # type: ignore[no-untyped-def]
        return {
            key: {
                "proof": r["proof"].model_dump(mode="json") if r["proof"] else None,
                "detail": r["detail"],
            }
            for (run, key), r in self.rows.items()
            if run == run_id
        }

    async def get_meta(self, run_id):  # type: ignore[no-untyped-def]
        return self.meta.get(run_id)

    async def update_row(
        self,
        run_id,
        tenant_id,
        key,
        old_version,
        from_status,
        new_status,  # type: ignore[no-untyped-def]
        new_proof,
        reason,
        actor,
        *,
        stage,
        bump_attempt,
        exhausted_at,
        swept=False,
        detail=None,
        no_progress=False,
    ):
        r = self.rows[(run_id, key)]
        if r["version"] != old_version:
            raise self.VersionConflict(key)
        r.update(
            status=new_status,
            proof=new_proof,
            reason=reason,
            version=old_version + 1,
            attempts=r["attempts"] + int(bump_attempt),
            swept=r["swept"] or swept,
            detail=detail or r["detail"],
        )
        seq = 1 + sum(1 for e in self.event_log if e["run"] == run_id and e["key"] == key)
        self.event_log.append(
            {
                "run": run_id,
                "key": key,
                "seq": seq,
                "from": from_status,
                "to": new_status,
                "actor": actor,
            }
        )
