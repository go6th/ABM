"""Eval results in Postgres (migration 0018): ``eval_runs``, ``eval_case_results``,
``eval_metrics`` and ``eval_baselines``, all insert-only under a dedicated synthetic evaluation
tenant (forced RLS still applies). A baseline is a row pointing at a green run; a better baseline
is a new row, the latest for a branch wins (E8).

GUARANTEE(test_eval_store_round_trip_and_insert_only): E8 on Postgres.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.baseline import Provenance, from_json, to_json
from evalgate.gates import GateResult
from evalgate.runner import EvalResult

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


async def record(
    txn: TxnFactory,
    eval_tenant: UUID,
    result: EvalResult,
    gate: GateResult,
    prov: Provenance,
    seed: int,
) -> UUID:
    """Insert one eval run with its cases and metrics; returns its id."""
    async with txn(eval_tenant) as s:
        run_id: Any = (
            await s.execute(
                text(
                    "INSERT INTO eval_runs (mode, status, code_sha, prompts_hash, routing_hash,"
                    " golden_version, seed, report_json) VALUES (:m, :st, :c, :p, :r, :g, :seed,"
                    " CAST(:rep AS jsonb)) RETURNING id"
                ),
                {
                    "m": prov.mode,
                    "st": gate.status,
                    "c": prov.code_sha,
                    "p": prov.prompts_hash,
                    "r": prov.routing_hash,
                    "g": prov.golden_version,
                    "seed": seed,
                    "rep": json.dumps(to_json(result, prov), sort_keys=True),
                },
            )
        ).scalar_one()
        for case in result.cases:
            await s.execute(
                text(
                    "INSERT INTO eval_case_results (eval_run_id, account, repeat_no,"
                    " result_json) VALUES (:e, :a, :r, CAST(:j AS jsonb))"
                ),
                {
                    "e": run_id,
                    "a": case.account,
                    "r": case.repeat,
                    "j": json.dumps(dict(case.values), sort_keys=True),
                },
            )
        for name, value in sorted(result.metrics.items()):
            base = result.bases.get(name)
            await s.execute(
                text(
                    "INSERT INTO eval_metrics (eval_run_id, metric, value, numerator,"
                    " denominator, status) VALUES (:e, :m, :v, :n, :d, :st)"
                ),
                {
                    "e": run_id,
                    "m": name,
                    "v": value,
                    "n": base[0] if base else None,
                    "d": base[1] if base else None,
                    "st": "measured" if value is not None else "not_applicable",
                },
            )
    return UUID(str(run_id))


async def promote_baseline(
    txn: TxnFactory, eval_tenant: UUID, eval_run_id: UUID, branch: str, code_sha: str
) -> None:
    """Point the branch's baseline at a green run (a new row; earlier ones stay)."""
    async with txn(eval_tenant) as s:
        status: Any = (
            await s.execute(text("SELECT status FROM eval_runs WHERE id = :e"), {"e": eval_run_id})
        ).scalar_one()
        if status != "pass":
            raise ValueError("only a green eval run can become a baseline")
        await s.execute(
            text("INSERT INTO eval_baselines (eval_run_id, branch, code_sha) VALUES (:e, :b, :c)"),
            {"e": eval_run_id, "b": branch, "c": code_sha},
        )


async def latest_baseline(
    txn: TxnFactory, eval_tenant: UUID, branch: str
) -> tuple[EvalResult, Provenance] | None:
    """The newest baseline of a branch, or None."""
    async with txn(eval_tenant) as s:
        row = (
            await s.execute(
                text(
                    "SELECT r.report_json FROM eval_baselines b JOIN eval_runs r"
                    " ON r.id = b.eval_run_id WHERE b.branch = :b ORDER BY b.id DESC LIMIT 1"
                ),
                {"b": branch},
            )
        ).first()
    return None if row is None else from_json(dict(row.report_json))
