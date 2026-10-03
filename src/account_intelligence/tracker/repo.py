"""``CoverageRepo``: tracker persistence on the caller's tenant transaction (``tenant_txn``).

The repository is constructed on an ``AsyncSession`` already bound to a tenant, so RLS scopes
every statement and the tracker writes commit or roll back with the stage's fact writes (spec
8.1). Every state change writes its ``coverage_row_events`` row in the same transaction, with the
status it came FROM read before the update (finding SC-3). Updates are optimistic: ``WHERE
version = :v``; zero rows raises ``VersionConflict`` for the service's bounded re-read loop.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.tracker.config import ChecklistRow
from account_intelligence.tracker.errors import RunNotSeeded, UnknownRow, VersionConflict
from account_intelligence.tracker.gap import TrackerRowState
from account_intelligence.tracker.model import Actor, Proof, ReasonCode, Status


@dataclass(frozen=True)
class StoredRow:
    """A row as stored, with its optimistic version and proof."""

    state: TrackerRowState
    version: int
    proof: Proof | None
    owner_stage: str | None
    detail: str | None


def _state(row: Any) -> TrackerRowState:
    return TrackerRowState(
        key=row.row_key,
        priority=row.priority,
        status=Status(row.status) if row.status else None,
        reason_code=ReasonCode(row.reason_code) if row.reason_code else None,
        proof_hash=row.proof_hash,
        swept=row.swept,
        proof_calls=row.proof_calls,
        attempts=row.attempts,
    )


_COLUMNS = (
    "row_key, priority, status, reason_code, proof, proof_hash, swept, attempts, version,"
    " owner_stage, detail, proof_calls"
)


class CoverageRepo:
    """Postgres implementation; the only module that writes tracker tables."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def seed(
        self,
        run_id: UUID,
        tenant_id: UUID,
        rows: Sequence[ChecklistRow],
        checklist_version: str,
        checklist_hash: str,
        policy: Mapping[str, Any],
        owners: Mapping[str, str],
    ) -> bool:
        """Insert the meta row and every row unset. Returns False when already seeded (no-op)."""
        inserted = await self.session.execute(
            text(
                "INSERT INTO run_coverage_meta (run_id, tenant_id, checklist_version,"
                " checklist_hash, seeded_row_count, pinned_policy)"
                " VALUES (:run_id, :tenant_id, :version, :hash, :count, CAST(:policy AS jsonb))"
                " ON CONFLICT (run_id) DO NOTHING RETURNING run_id"
            ),
            {
                "run_id": run_id,
                "tenant_id": tenant_id,
                "version": checklist_version,
                "hash": checklist_hash,
                "count": len(rows),
                "policy": json.dumps(dict(policy), sort_keys=True),
            },
        )
        if inserted.first() is None:
            return False
        for row in rows:
            await self.session.execute(
                text(
                    "INSERT INTO coverage_rows (run_id, row_key, tenant_id, priority, owner_stage)"
                    " VALUES (:run_id, :row_key, :tenant_id, :priority, :owner)"
                ),
                {
                    "run_id": run_id,
                    "row_key": row.key,
                    "tenant_id": tenant_id,
                    "priority": row.priority,
                    "owner": owners.get(row.key),
                },
            )
        return True

    async def get_row(self, run_id: UUID, row_key: str) -> StoredRow:
        """One row; ``UnknownRow`` when the run has no such row."""
        row = (
            await self.session.execute(
                # _COLUMNS is a module constant; all values are bound parameters.
                text(f"SELECT {_COLUMNS} FROM coverage_rows WHERE run_id = :r AND row_key = :k"),  # nosec B608
                {"r": run_id, "k": row_key},
            )
        ).first()
        if row is None:
            raise UnknownRow(row_key)
        return StoredRow(
            state=_state(row),
            version=row.version,
            proof=Proof.model_validate(row.proof) if row.proof is not None else None,
            owner_stage=row.owner_stage,
            detail=row.detail,
        )

    async def get_all_rows(self, run_id: UUID) -> list[TrackerRowState]:
        """Every row of the run, in key order."""
        result = await self.session.execute(
            # _COLUMNS is a module constant; all values are bound parameters.
            text(f"SELECT {_COLUMNS} FROM coverage_rows WHERE run_id = :r ORDER BY row_key"),  # nosec B608
            {"r": run_id},
        )
        return [_state(r) for r in result]

    async def get_proofs(self, run_id: UUID) -> dict[str, dict[str, Any]]:
        """Proof JSON per row key (operator view only)."""
        result = await self.session.execute(
            text("SELECT row_key, proof, detail FROM coverage_rows WHERE run_id = :r"),
            {"r": run_id},
        )
        return {r.row_key: {"proof": r.proof, "detail": r.detail} for r in result}

    async def get_meta(self, run_id: UUID) -> dict[str, Any] | None:
        """The pinned checklist version, policy and seeded count, or ``None`` when unseeded."""
        row = (
            await self.session.execute(
                text(
                    "SELECT checklist_version, checklist_hash, pinned_policy, seeded_row_count"
                    " FROM run_coverage_meta WHERE run_id = :r"
                ),
                {"r": run_id},
            )
        ).first()
        if row is None:
            return None
        policy = row.pinned_policy
        return {
            "checklist_version": row.checklist_version,
            "checklist_hash": row.checklist_hash,
            "pinned_policy": policy if isinstance(policy, dict) else json.loads(policy),
            "seeded_row_count": row.seeded_row_count,
        }

    async def require_meta(self, run_id: UUID) -> dict[str, Any]:
        """The meta row or ``RunNotSeeded``."""
        meta = await self.get_meta(run_id)
        if meta is None:
            raise RunNotSeeded(str(run_id))
        return meta

    async def update_row(
        self,
        run_id: UUID,
        tenant_id: UUID,
        row_key: str,
        old_version: int,
        from_status: Status | None,
        new_status: Status,
        new_proof: Proof,
        reason_code: ReasonCode | None,
        actor: Actor,
        *,
        stage: str | None,
        bump_attempt: bool,
        exhausted_at: int,
        swept: bool = False,
        detail: str | None = None,
        no_progress: bool = False,
    ) -> None:
        """Optimistic update plus its event, one transaction. Zero rows: ``VersionConflict``."""
        result = await self.session.execute(
            text(
                "UPDATE coverage_rows SET status = :status, reason_code = :reason,"
                " proof = CAST(:proof AS jsonb), proof_hash = :hash, proof_calls = :calls,"
                " version = version + 1, attempts = attempts + :bump,"
                " exhausted = (attempts + :bump) >= :cap,"
                " no_progress_count = no_progress_count + :np,"
                " swept = (swept OR :swept), detail = COALESCE(:detail, detail), last_checked_at"
                " = now()"
                " WHERE run_id = :run_id AND row_key = :row_key AND version = :version"
            ),
            {
                "status": new_status.value,
                "reason": reason_code.value if reason_code else None,
                "proof": new_proof.model_dump_json(),
                "hash": new_proof.proof_hash,
                "calls": len(set(new_proof.call_refs)),
                "bump": 1 if bump_attempt else 0,
                "cap": exhausted_at,
                "np": 1 if no_progress else 0,
                "swept": swept,
                "detail": detail,
                "run_id": run_id,
                "row_key": row_key,
                "version": old_version,
            },
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise VersionConflict(f"{row_key}: version {old_version} is stale")
        await self.session.execute(
            text(
                "INSERT INTO coverage_row_events (tenant_id, run_id, row_key, from_status,"
                " to_status, actor, stage, proof_hash, reason_code)"
                " VALUES (:tenant_id, :run_id, :row_key, :from_status, :to_status, :actor,"
                " :stage, :hash, :reason)"
            ),
            {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "row_key": row_key,
                "from_status": from_status.value if from_status else None,
                "to_status": new_status.value,
                "actor": actor.value,
                "stage": stage,
                "hash": new_proof.proof_hash,
                "reason": reason_code.value if reason_code else None,
            },
        )

    async def events(self, run_id: UUID) -> list[dict[str, Any]]:
        """The run's events in order (tests and the operator view)."""
        result = await self.session.execute(
            text(
                "SELECT row_key, seq, from_status, to_status, actor, stage, proof_hash, reason_code"
                " FROM coverage_row_events WHERE run_id = :r ORDER BY row_key, seq"
            ),
            {"r": run_id},
        )
        return [dict(r._mapping) for r in result]
