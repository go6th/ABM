"""Create runs and read their persisted state.

Guarantees: a tenant-scoped idempotency claim encloses account/run/wallet writes
(GUARANTEE(test_idempotency_rejects_key_reuse_with_different_request)); reads use
tenant_txn and return indistinguishable 404s for absent or foreign resources.
"""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

import inngest
from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.agents.graph import GRAPH_VERSION
from account_intelligence.api.auth import Identity, require_role
from account_intelligence.api.domains import validate_domain
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.etag import if_none_match_matches
from account_intelligence.api.idempotency import canonical_request_hash, replay_response
from account_intelligence.api.pricing import credits_for_depth
from account_intelligence.config import get_settings
from account_intelligence.db.session import tenant_txn
from account_intelligence.orchestrator.functions import client as inngest_client

router = APIRouter(prefix="/runs", tags=["runs"])
COST = {"standard": 6.0, "deep": 18.0}  # credits reserved up front (calibrate with data)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$", max_length=253)
    depth: Literal["standard", "deep"] = "standard"

    @field_validator("domain", mode="before")
    @classmethod
    def normalize_domain(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value


class RunCreatedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    status: str


class RunStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    status: str
    created_at: datetime


async def _existing_response(
    session: AsyncSession,
    *,
    idempotency_key: str,
    request_hash: str,
) -> Response | None:
    """Return a stored replay response or raise for a conflicting key."""
    previous = (
        await session.execute(
            text(
                "SELECT endpoint, request_hash, status, response_json, resource_id "
                "FROM idempotency_records "
                "WHERE tenant_id = (SELECT app_tenant()) AND key = :key"
            ),
            {"key": idempotency_key},
        )
    ).one_or_none()
    if previous is None:
        return None
    if previous.endpoint != "/v1/runs":
        raise APIProblem(
            409,
            "idempotency_key_reuse",
            "Idempotency key conflict",
            "The idempotency key was already used for a different request.",
        )
    response_json = replay_response(
        stored_hash=previous.request_hash,
        incoming_hash=request_hash,
        stored_response=previous.response_json,
    )
    if previous.status is None or not isinstance(response_json, str):
        raise APIProblem(
            409,
            "request_in_progress",
            "Request in progress",
            "The original request has not completed.",
        )
    try:
        stored_response = json.loads(response_json)
    except json.JSONDecodeError:
        stored_response = None
    if (
        not isinstance(stored_response, dict)
        or not isinstance(stored_response.get("run_id"), str)
        or not isinstance(stored_response.get("status"), str)
        or previous.status != 202
        or str(previous.resource_id) != stored_response.get("run_id")
    ):
        raise APIProblem(
            503,
            "idempotency_unavailable",
            "Service temporarily unavailable",
            "The stored request result is invalid.",
        )
    return Response(
        content=response_json,
        status_code=previous.status,
        media_type="application/json",
    )


@router.post("", status_code=202, response_model=RunCreatedResponse)
async def create_run(
    body: RunRequest,
    request: Request,
    idem: str = Header(alias="Idempotency-Key", min_length=8, max_length=64),
    ident: Identity = Depends(require_role("admin", "seller")),
) -> Response:
    request_hash = canonical_request_hash(body.model_dump(mode="json"))
    async with tenant_txn(ident.tenant_id) as session:
        previous_response = await _existing_response(
            session,
            idempotency_key=idem,
            request_hash=request_hash,
        )
    if previous_response is not None:
        return previous_response

    resolver = request.app.state.domain_resolver
    blocked_entities = request.app.state.blocked_entity_port
    domain = await validate_domain(
        body.domain,
        resolver=resolver,
        blocked_entities=blocked_entities,
    )
    settings = get_settings()
    credits = credits_for_depth(
        body.depth,
        standard_credits=settings.run_credits_standard,
        deep_credits=settings.run_credits_deep,
    )
    run_id = uuid4()
    response: dict[str, str]
    async with tenant_txn(ident.tenant_id) as session:
        claim = (
            await session.execute(
                text(
                    "INSERT INTO idempotency_records "
                    "(tenant_id, key, endpoint, request_hash) "
                    "VALUES ((SELECT app_tenant()), :key, '/v1/runs', :request_hash) "
                    "ON CONFLICT (tenant_id, key) DO NOTHING RETURNING key"
                ),
                {"key": idem, "request_hash": request_hash},
            )
        ).first()
        if claim is None:
            previous_response = await _existing_response(
                session,
                idempotency_key=idem,
                request_hash=request_hash,
            )
            if previous_response is None:
                raise APIProblem(
                    503,
                    "idempotency_unavailable",
                    "Service temporarily unavailable",
                    "The request could not be safely deduplicated.",
                )
            return previous_response

        account = (
            await session.execute(
                text(
                    "INSERT INTO accounts (tenant_id, domain) "
                    "VALUES ((SELECT app_tenant()), :domain) "
                    "ON CONFLICT (tenant_id, domain) DO NOTHING RETURNING id"
                ),
                {"domain": domain},
            )
        ).first()
        if account is None:
            account = (
                await session.execute(
                    text(
                        "SELECT id FROM accounts "
                        "WHERE tenant_id = (SELECT app_tenant()) AND domain = :domain"
                    ),
                    {"domain": domain},
                )
            ).one_or_none()
        if account is None:
            raise APIProblem(
                503,
                "account_unavailable",
                "Service temporarily unavailable",
                "The account could not be created or loaded.",
            )

        claimed = (
            await session.execute(
                text(
                    "INSERT INTO account_runs "
                    "(id, tenant_id, account_id, graph_version, budget_json, "
                    "reserved_credits, idempotency_key) "
                    "VALUES (:run_id, (SELECT app_tenant()), :account_id, :graph_version, "
                    "CAST(:budget AS jsonb), :credits, :key) "
                    "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING id"
                ),
                {
                    "run_id": run_id,
                    "account_id": account.id,
                    "graph_version": GRAPH_VERSION,
                    "budget": json.dumps({"usd": settings.run_budget_usd}),
                    "credits": credits,
                    "key": idem,
                },
            )
        ).first()
        if claimed is None:
            legacy_run = (
                await session.execute(
                    text(
                        "SELECT id FROM account_runs "
                        "WHERE tenant_id = (SELECT app_tenant()) AND idempotency_key = :key"
                    ),
                    {"key": idem},
                )
            ).one_or_none()
            if legacy_run is not None:
                raise APIProblem(
                    409,
                    "idempotency_key_reuse",
                    "Idempotency key conflict",
                    "An earlier request cannot be safely replayed.",
                )
            raise APIProblem(
                503,
                "run_creation_conflict",
                "Service temporarily unavailable",
                "Run creation could not be completed safely.",
            )

        wallet = (
            await session.execute(
                text(
                    "UPDATE wallets SET balance = balance - :credits "
                    "WHERE tenant_id = (SELECT app_tenant()) "
                    "AND balance >= :credits RETURNING balance"
                ),
                {"credits": credits},
            )
        ).first()
        if wallet is None:
            raise APIProblem(
                402,
                "insufficient_credits",
                "Payment required",
                "There are not enough credits to create this run.",
            )
        await session.execute(
            text(
                "INSERT INTO wallet_ledger "
                "(tenant_id, run_id, entry, amount, balance_after) "
                "VALUES ((SELECT app_tenant()), :run_id, 'reserve', :amount, :balance)"
            ),
            {"run_id": run_id, "amount": -credits, "balance": wallet.balance},
        )
        response = {"run_id": str(run_id), "status": "QUEUED"}
        response_body = bytes(JSONResponse(status_code=202, content=response).body)
        response_json = response_body.decode("utf-8")
        updated_record = await session.execute(
            text(
                "UPDATE idempotency_records SET status = 202, "
                "response_json = :response, resource_id = :run_id "
                "WHERE tenant_id = (SELECT app_tenant()) AND key = :key "
                "RETURNING key"
            ),
            {
                "response": response_json,
                "run_id": run_id,
                "key": idem,
            },
        )
        if updated_record.scalar_one_or_none() is None:
            raise APIProblem(
                503,
                "idempotency_unavailable",
                "Service temporarily unavailable",
                "The request result could not be stored safely.",
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
    return Response(content=response_body, status_code=202, media_type="application/json")


@router.get("/{run_id}", response_model=RunStatusResponse)
async def get_run(
    run_id: UUID,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    ident: Identity = Depends(require_role("admin", "seller", "viewer")),
) -> Response:
    """Return the persisted tenant-scoped run state with a representation ETag."""
    async with tenant_txn(ident.tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT id, status, created_at FROM account_runs WHERE id = :run_id"),
                {"run_id": run_id},
            )
        ).one_or_none()
    if row is None:
        raise APIProblem(404, "not_found", "Not found", "The requested run was not found.")

    payload = RunStatusResponse(run_id=row.id, status=row.status, created_at=row.created_at)
    response_payload = jsonable_encoder(payload)
    serialized = json.dumps(response_payload, sort_keys=True, separators=(",", ":")).encode()
    etag = f'"{sha256(serialized).hexdigest()}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if if_none_match_matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    return JSONResponse(status_code=200, content=response_payload, headers=headers)
