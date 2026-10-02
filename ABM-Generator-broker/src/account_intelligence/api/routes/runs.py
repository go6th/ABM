"""POST /v1/runs: create a run. Idempotent, credit-reserving, outbox-safe."""

from __future__ import annotations

import json
from typing import Literal
from uuid import uuid4

import inngest
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from account_intelligence.agents.graph import GRAPH_VERSION
from account_intelligence.api.auth import Identity, require_role
from account_intelligence.config import get_settings
from account_intelligence.db.session import tenant_txn
from account_intelligence.orchestrator.functions import client as inngest_client

router = APIRouter(prefix="/runs", tags=["runs"])
COST = {"standard": 6.0, "deep": 18.0}  # credits reserved up front (calibrate with data)


class RunRequest(BaseModel):
    domain: str = Field(pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$", max_length=253)
    depth: Literal["standard", "deep"] = "standard"


@router.post("", status_code=202)
async def create_run(
    body: RunRequest,
    idem: str = Header(alias="Idempotency-Key", min_length=8, max_length=64),
    ident: Identity = Depends(require_role("admin", "seller")),
) -> dict[str, str]:
    est = COST[body.depth]
    run_id = uuid4()
    async with tenant_txn(ident.tenant_id) as s:
        claimed = (
            await s.execute(
                text(
                    "WITH a AS ("
                    " INSERT INTO accounts (tenant_id, domain) VALUES ((SELECT app_tenant()), :d) "
                    " ON CONFLICT (tenant_id, domain) DO UPDATE "
                    "SET domain = EXCLUDED.domain RETURNING id) "
                    "INSERT INTO account_runs (id, tenant_id, account_id, graph_version, "
                    "budget_json, reserved_credits, idempotency_key) "
                    "SELECT :id, (SELECT app_tenant()), a.id, :gv, CAST(:b AS jsonb), "
                    ":est, :k FROM a "
                    "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING id"
                ),
                {
                    "id": run_id,
                    "gv": GRAPH_VERSION,
                    "b": json.dumps({"usd": get_settings().run_budget_usd}),
                    "est": est,
                    "k": idem,
                    "d": body.domain,
                },
            )
        ).first()
        if claimed is None:  # replay of an earlier request
            prev = (
                await s.execute(
                    text("SELECT id, status FROM account_runs WHERE idempotency_key = :k"),
                    {"k": idem},
                )
            ).one()
            return {"run_id": str(prev.id), "status": prev.status}
        ok = (
            await s.execute(
                text(  # atomic: only succeeds if balance suffices
                    "UPDATE wallets SET balance = balance - :e "
                    "WHERE tenant_id = (SELECT app_tenant()) "
                    "AND balance >= :e RETURNING balance"
                ),
                {"e": est},
            )
        ).first()
        if ok is None:
            raise HTTPException(402, "insufficient credits")  # raising rolls the whole txn back
        await s.execute(
            text(
                "INSERT INTO wallet_ledger (tenant_id, run_id, entry, amount, balance_after) "
                "VALUES ((SELECT app_tenant()), :r, 'reserve', :e, :bal)"
            ),
            {"r": run_id, "e": -est, "bal": ok.balance},
        )
    await inngest_client.send(
        inngest.Event(
            id=str(run_id),
            name="research/run.requested",
            data={
                "tenant_id": str(ident.tenant_id),
                "run_id": str(run_id),
            },
        )
    )
    return {"run_id": str(run_id), "status": "QUEUED"}
