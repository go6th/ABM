"""Operator observability endpoints (O15): identifiers and amounts only, no content, never a tenant
role. Fail closed until the operator plane exists (``require_operator``, D-33).

* ``GET /operator/v1/runs/{id}/timeline``: the event-derived timeline (units, queue and lease
  waits, critical path, idle time) and the trace id;
* ``GET /operator/v1/ops/cost/{run_id}``: the ledger-derived rollup and its reconciliation;
* ``GET /operator/v1/ops/failures``: failed and partial runs by reason (bounded);
* ``GET /operator/v1/ops/stuck``: the latest stuck-run observations (bounded).

GUARANTEE(test_ops_routes_are_operator_only_and_carry_no_content): O15.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from account_intelligence.api.operator import OperatorIdentity, require_operator
from account_intelligence.db.session import tenant_txn
from obs import costs, timings
from obs.context import trace_id_for

router = APIRouter(prefix="/operator/v1", tags=["ops"])
MAX_ROWS = 200


@router.get("/runs/{run_id}/timeline")
async def run_timeline(
    run_id: UUID, op: OperatorIdentity = Depends(require_operator)
) -> dict[str, Any]:
    """The run's timeline from durable events."""
    try:
        tl = await timings.timeline(tenant_txn, op.tenant_id, run_id)
    except LookupError:
        raise HTTPException(404, "run not found") from None
    return {
        "run_id": tl.run_id,
        "trace_id": f"{trace_id_for(run_id):032x}",
        "status": tl.status,
        "created_at": tl.created_at.isoformat(),
        "finished_at": tl.finished_at.isoformat() if tl.finished_at else None,
        "makespan_s": tl.units.makespan_s,
        "critical_path_s": tl.units.critical_path_s,
        "idle_s": tl.units.idle_s,
        "missing_sources": list(tl.missing),
        "units": [
            {
                "unit_key": t.key,
                "queue_wait_s": t.queue_wait_s,
                "run_s": t.run_s,
                "lease_wait_s": t.lease_wait_s,
                "outcome": t.outcome,
            }
            for t in tl.units.timings
        ][:MAX_ROWS],
    }


@router.get("/ops/cost/{run_id}")
async def run_cost(
    run_id: UUID, op: OperatorIdentity = Depends(require_operator)
) -> dict[str, Any]:
    """The run's cost rollup (refreshed) and its reconciliation with the ledger."""
    lines = await costs.refresh(tenant_txn, op.tenant_id, run_id)
    return {
        "run_id": str(run_id),
        "lines": [
            {
                "provider_code": ln.provider_code,
                "route": ln.route,
                "cost_source": ln.cost_source,
                "calls": ln.calls,
                "cost_micros": ln.cost_micros,
                "unknown_cost_calls": ln.unknown_cost_calls,
            }
            for ln in lines
        ],
        "reconcile": await costs.reconcile(tenant_txn, op.tenant_id, run_id),
    }


@router.get("/ops/failures")
async def failures(op: OperatorIdentity = Depends(require_operator)) -> dict[str, Any]:
    """Failed and partial runs by status and reason (counts only)."""
    async with tenant_txn(op.tenant_id) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT status, COALESCE(finished_reason, 'none') AS reason, count(*) AS runs"
                    " FROM account_runs WHERE status IN ('FAILED', 'PARTIAL', 'BUDGET_EXCEEDED')"
                    " GROUP BY status, reason ORDER BY runs DESC LIMIT :n"
                ),
                {"n": MAX_ROWS},
            )
        ).all()
    return {"rows": [{"status": r.status, "reason": r.reason, "runs": int(r.runs)} for r in rows]}


@router.get("/ops/stuck")
async def stuck(op: OperatorIdentity = Depends(require_operator)) -> dict[str, Any]:
    """The latest stuck-run observations (identifiers and reasons only)."""
    async with tenant_txn(op.tenant_id) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT run_id, state, reason, age_bucket, at FROM stuck_run_observations"
                    " ORDER BY id DESC LIMIT :n"
                ),
                {"n": MAX_ROWS},
            )
        ).all()
    return {
        "rows": [
            {
                "run_id": str(r.run_id),
                "state": r.state,
                "reason": r.reason,
                "age_bucket": r.age_bucket,
                "at": r.at.isoformat(),
            }
            for r in rows
        ]
    }
