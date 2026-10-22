"""``POST /v1/runs`` in input mode "file" (``routes.file_runs``): anonymous is 401 before any
database work, a viewer is 403 (the catalog admits admins and sellers, so analysts too), and a
seller's request reaches the file-run handler, which answers with its typed problem when a
document is unknown (404 ``document_not_found``). The full file run to COMPLETED is covered by
``tests/documents/test_file_runs.py`` on the hermetic stack. SYNTHETIC data only."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from account_intelligence.api.auth import Identity, identity
from account_intelligence.api.main import create_app
from account_intelligence.api.policy import policy_for
from tests.phase3.conftest import BASE, Plane, add_member, make_app, new_tenant


def file_body(*documents: UUID) -> dict[str, Any]:
    return {"input_mode": "file", "file": {"document_ids": [str(d) for d in documents]}}


def idem() -> dict[str, str]:
    return {"Idempotency-Key": uuid4().hex}


def test_an_anonymous_file_run_is_refused_before_any_database_work() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    assert client.post("/v1/runs", json=file_body(uuid4()), headers=idem()).status_code == 401


def tenant_plane(tenant: UUID, role: str, user: str) -> Plane:
    plane = make_app()
    add_member(tenant, user, role)
    now: datetime = plane.clock.now()

    async def ident() -> Identity:
        return Identity(user, tenant, role, now)

    plane.app.dependency_overrides[identity] = ident
    return plane


@asynccontextmanager
async def client(plane: Plane) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=plane.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_viewer_cannot_start_a_file_run() -> None:
    policy = policy_for("POST", "/v1/runs")
    assert policy is not None and "viewer" not in policy.roles
    tenant = new_tenant("file-run-viewer")
    async with client(tenant_plane(tenant, "viewer", "file-viewer")) as c:
        refused = await c.post("/v1/runs", json=file_body(uuid4()), headers=idem())
        assert refused.status_code == 403


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_seller_reaches_the_file_run_handler_which_refuses_an_unknown_document() -> None:
    tenant = new_tenant("file-run-seller")
    async with client(tenant_plane(tenant, "seller", "file-seller")) as c:
        reply = await c.post("/v1/runs", json=file_body(uuid4()), headers=idem())
        assert reply.status_code == 404, reply.text
        problem = reply.json()
        assert problem["type"].endswith("document_not_found")
        assert problem["title"] == "Document not found"
        # the idempotency key is required on every run start
        missing = await c.post("/v1/runs", json=file_body(uuid4()))
        assert missing.status_code == 422
