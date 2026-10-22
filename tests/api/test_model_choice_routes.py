"""The model choice routes (``routes.model_choice``): anonymous is 401 and a viewer cannot save
preferences (403, ``save_own_view`` is for admins, analysts and sellers), both before any
database work; a viewer reads the menus and an analyst saves the default research model back
and reads it again. The fake keys only make the models "set up"; nothing calls a provider.
SYNTHETIC data only."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from account_intelligence.api.auth import Identity, identity
from account_intelligence.api.main import create_app
from account_intelligence.api.policy import policy_for
from tests.phase3.conftest import BASE, Plane, add_member, make_app, new_tenant


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    monkeypatch.setenv("MOONSHOT_API_KEY", "test-not-a-key")


def test_anonymous_callers_and_viewers_saving_are_refused_before_any_database_work() -> None:
    save = policy_for("PUT", "/v1/me/model-preferences")
    assert save is not None and "viewer" not in save.roles
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/v1/models/choices").status_code == 401
    assert client.put("/v1/me/model-preferences", json={}).status_code == 401

    async def viewer() -> Identity:
        return Identity("model-viewer", uuid4(), "viewer", datetime.now(UTC))

    app.dependency_overrides[identity] = viewer
    assert client.put("/v1/me/model-preferences", json={}).status_code == 403


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
async def test_a_viewer_reads_the_menus_and_an_analyst_saves_a_choice(keys: None) -> None:
    tenant = new_tenant("model-choice")
    async with client(tenant_plane(tenant, "viewer", "model-viewer")) as c:
        menus = await c.get("/v1/models/choices")
        assert menus.status_code == 200, menus.text
        body: dict[str, Any] = menus.json()
        assert {"research", "analysis", "models"} <= set(body)
        assert body["research"] in {m["id"] for m in body["models"]}
        assert (await c.put("/v1/me/model-preferences", json={})).status_code == 403
    async with client(tenant_plane(tenant, "analyst", "model-analyst")) as c:
        before = (await c.get("/v1/models/choices")).json()
        saved = await c.put("/v1/me/model-preferences", json={"research_model": before["research"]})
        assert saved.status_code == 200, saved.text
        assert saved.json()["research"] == before["research"]
        unknown = await c.put("/v1/me/model-preferences", json={"research_model": "no-such"})
        assert unknown.status_code == 422
        assert (await c.put("/v1/me/model-preferences", json={"other": 1})).status_code == 422
