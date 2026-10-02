from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import DataLayerError
from src.db.repositories.checklist import load_checklist, validate_coverage_proof
from src.db.repositories.contracts import CoverageStatus


async def seed_coverage_rows(session: AsyncSession, run_id: UUID) -> int:
    """Seed all checklist rows from versioned config within the caller's tenant transaction."""
    version, rows = load_checklist()
    tenant_id = (await session.execute(text("SELECT app_tenant()"))).scalar_one_or_none()
    if tenant_id is None:
        raise DataLayerError("tenant context is required to seed coverage")
    run = (
        await session.execute(
            text(
                "SELECT account_id FROM account_runs "
                "WHERE tenant_id = :tenant_id AND id = :run_id FOR UPDATE"
            ),
            {"tenant_id": tenant_id, "run_id": run_id},
        )
    ).one_or_none()
    if run is None:
        raise DataLayerError(f"run {run_id} is not visible in this tenant")
    await session.execute(
        text(
            "UPDATE account_runs SET checklist_version = :version, expected_coverage_rows = :count "
            "WHERE tenant_id = :tenant_id AND id = :run_id"
        ),
        {"version": version, "count": len(rows), "tenant_id": tenant_id, "run_id": run_id},
    )
    for row in rows:
        await session.execute(
            text(
                "INSERT INTO coverage_rows (tenant_id, account_id, run_id, checklist_version, "
                "row_key, pass, description, priority) VALUES (:tenant_id, :account_id, :run_id, "
                ":version, :row_key, :pass, :description, :priority) "
                "ON CONFLICT (tenant_id, run_id, row_key) DO NOTHING"
            ),
            {
                "tenant_id": tenant_id,
                "account_id": run.account_id,
                "run_id": run_id,
                "version": version,
                "row_key": row.row_id,
                "pass": row.pass_number,
                "description": row.label,
                "priority": row.priority,
            },
        )
    return len(rows)

async def record_coverage(
    session: AsyncSession,
    *,
    run_id: UUID,
    row_key: str,
    status: CoverageStatus,
    queries: list[str] | None = None,
    tool_categories: list[str] | None = None,
    result_count: int | None = None,
    blocked_reason: str | None = None,
) -> None:
    queries = queries or []
    tool_categories = tool_categories or []
    validate_coverage_proof(status, queries, tool_categories, result_count, blocked_reason)
    tenant_id = (await session.execute(text("SELECT app_tenant()"))).scalar_one_or_none()
    if tenant_id is None:
        raise DataLayerError("tenant context is required to update coverage")
    result = await session.execute(
        text(
            "UPDATE coverage_rows SET status = :status, queries = CAST(:queries AS jsonb), "
            "tool_categories = CAST(:tool_categories AS jsonb), result_count = :result_count, "
            "blocked_reason = :blocked_reason, last_checked_at = now() "
            "WHERE tenant_id = :tenant_id AND run_id = :run_id AND row_key = :row_key"
        ),
        {
            "status": status,
            "queries": json.dumps(queries),
            "tool_categories": json.dumps(tool_categories),
            "result_count": result_count,
            "blocked_reason": blocked_reason,
            "tenant_id": tenant_id,
            "run_id": run_id,
            "row_key": row_key,
        },
    )
    if result.rowcount != 1:
        raise DataLayerError(f"coverage row {row_key} is not seeded for this run")
