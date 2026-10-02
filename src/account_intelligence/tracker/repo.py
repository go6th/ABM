from typing import Any

"""Tracker repository using tenant_txn."""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.tracker.errors import VersionConflict
from account_intelligence.tracker.gap import TrackerRowState
from account_intelligence.tracker.model import Actor, Proof, ReasonCode, Status


class CoverageRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def seed(
        self,
        run_id: UUID,
        tenant_id: UUID,
        rows: list[dict[str, Any]],
        checklist_version: str,
        checklist_hash: str,
        policy: dict[str, Any],
    ) -> None:
        res = await self.session.execute(
            text("SELECT 1 FROM run_coverage_meta WHERE run_id = :run_id"), {"run_id": run_id}
        )
        if res.scalar():
            return

        await self.session.execute(
            text("""
            INSERT INTO run_coverage_meta (run_id, tenant_id, checklist_version, checklist_hash, seeded_row_count, pinned_policy)
            VALUES (:run_id, :tenant_id, :version, :hash, :count, :policy)
            """),
            {
                "run_id": run_id,
                "tenant_id": tenant_id,
                "version": checklist_version,
                "hash": checklist_hash,
                "count": len(rows),
                "policy": json.dumps(policy),
            },
        )

        for r in rows:
            await self.session.execute(
                text("""
                INSERT INTO coverage_rows (run_id, row_key, tenant_id, priority, owner_stage)
                VALUES (:run_id, :row_key, :tenant_id, :priority, :owner_stage)
                ON CONFLICT (run_id, row_key) DO NOTHING
                """),
                {
                    "run_id": run_id,
                    "row_key": r["key"],
                    "tenant_id": tenant_id,
                    "priority": r.get("priority", "optional"),
                    "owner_stage": r.get("owner_stage"),
                },
            )

    async def get_row(
        self, run_id: UUID, row_key: str
    ) -> tuple[TrackerRowState, int, Proof | None]:
        res = await self.session.execute(
            text(
                "SELECT priority, status, reason_code, proof, proof_hash, swept, attempts, version FROM coverage_rows WHERE run_id = :run_id AND row_key = :row_key"
            ),
            {"run_id": run_id, "row_key": row_key},
        )
        row = res.fetchone()
        if not row:
            raise KeyError(f"Row {row_key} not found")

        status = Status(row.status) if row.status else None
        reason = ReasonCode(row.reason_code) if row.reason_code else None
        proof = Proof.model_validate_json(row.proof) if row.proof else None

        state = TrackerRowState(
            key=row_key,
            priority=row.priority,
            status=status,
            reason_code=reason,
            proof_hash=row.proof_hash,
            swept=row.swept,
            attempts=row.attempts,
        )
        return state, row.version, proof

    async def get_all_rows(self, run_id: UUID) -> list[TrackerRowState]:
        res = await self.session.execute(
            text(
                "SELECT row_key, priority, status, reason_code, proof_hash, swept, attempts FROM coverage_rows WHERE run_id = :run_id"
            ),
            {"run_id": run_id},
        )
        rows = []
        for row in res.fetchall():
            status = Status(row.status) if row.status else None
            reason = ReasonCode(row.reason_code) if row.reason_code else None
            rows.append(
                TrackerRowState(
                    key=row.row_key,
                    priority=row.priority,
                    status=status,
                    reason_code=reason,
                    proof_hash=row.proof_hash,
                    swept=row.swept,
                    attempts=row.attempts,
                )
            )
        return rows

    async def get_meta(self, run_id: UUID) -> dict[str, Any]:
        res = await self.session.execute(
            text(
                "SELECT checklist_version, pinned_policy, seeded_row_count FROM run_coverage_meta WHERE run_id = :run_id"
            ),
            {"run_id": run_id},
        )
        row = res.fetchone()
        if not row:
            raise KeyError("Run not seeded")
        return {
            "checklist_version": row.checklist_version,
            "pinned_policy": row.pinned_policy,
            "seeded_row_count": row.seeded_row_count,
        }

    async def update_row(
        self,
        run_id: UUID,
        tenant_id: UUID,
        row_key: str,
        old_version: int,
        new_status: Status,
        new_proof: Proof,
        reason_code: ReasonCode | None,
        actor: Actor,
        bumped: bool,
        swept: bool = False,
    ) -> None:
        proof_hash = new_proof.proof_hash
        proof_json = new_proof.model_dump_json()

        status_val = new_status.value if new_status else None
        reason_val = reason_code.value if reason_code else None

        res = await self.session.execute(
            text("""
            UPDATE coverage_rows 
            SET status = :status, reason_code = :reason_code, proof = :proof, proof_hash = :proof_hash,
                version = version + 1, attempts = attempts + :bump, swept = :swept, last_checked_at = now()
            WHERE run_id = :run_id AND row_key = :row_key AND version = :version
            """),
            {
                "status": status_val,
                "reason_code": reason_val,
                "proof": proof_json,
                "proof_hash": proof_hash,
                "bump": 1 if bumped else 0,
                "swept": swept,
                "run_id": run_id,
                "row_key": row_key,
                "version": old_version,
            },
        )
        if res.rowcount == 0:  # type: ignore
            raise VersionConflict("Version conflict")

        await self.session.execute(
            text("""
            INSERT INTO coverage_row_events (tenant_id, run_id, row_key, from_status, to_status, actor, proof_hash, reason_code)
            VALUES (:tenant_id, :run_id, :row_key, (SELECT status FROM coverage_rows WHERE run_id = :run_id AND row_key = :row_key), :to_status, :actor, :proof_hash, :reason_code)
            """),
            {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "row_key": row_key,
                "to_status": status_val,
                "actor": actor.value,
                "proof_hash": proof_hash,
                "reason_code": reason_val,
            },
        )
