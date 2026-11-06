"""Routes with no screen yet answer 404 until the client's feature flag is on (fix prompt B, task 4;
ADR-0112; docs/audits/orphan-routes.md). ``@behind_flag`` gates the connector, CRM and monitoring
routes: a client without the flag sees exactly what an unknown path gives (404, same body), a
client with it gets the route. Flags are deny by default (``admin/flags.evaluate``).
SYNTHETIC tenants only."""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import httpx
import psycopg
import pytest
from fastapi import Request

from account_intelligence.api.flags import GATED_FLAGS, behind_flag, flag_of
from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import BASE, add_member, make_app, new_tenant

pytestmark = pytest.mark.requires_postgres


def _set_rule(flag: str, rule: dict[str, object]) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO feature_flags (flag, version, rule, created_by, created_at)"
            " SELECT %s, coalesce(max(version), 0) + 1, %s::jsonb, 'test', now()"
            " FROM feature_flags WHERE flag = %s",
            (flag, json.dumps(rule), flag),
        )


@pytest.fixture
def flags_for() -> Iterator[list[UUID]]:
    """Each gated flag on for exactly the tenants the test appends (restored afterwards)."""
    on: list[UUID] = []
    with psycopg.connect(ADMIN_DSN) as c:
        before = {
            f: c.execute(
                "SELECT rule FROM feature_flags WHERE flag = %s ORDER BY version DESC LIMIT 1",
                (f,),
            ).fetchone()
            for f in GATED_FLAGS
        }
    yield on
    for f, row in before.items():
        _set_rule(f, row[0] if row else {"tenants": [], "plans": [], "percent": 0})


@asynccontextmanager
async def as_admin(tenant: UUID) -> AsyncIterator[httpx.AsyncClient]:
    from account_intelligence.api.auth import Identity, identity

    user = f"flag-admin-{tenant.hex[:6]}"
    add_member(tenant, user, "admin")
    plane = make_app()
    now = datetime.now(UTC)

    async def admin() -> Identity:
        return Identity(user, tenant, "admin", now)

    plane.app.dependency_overrides[identity] = admin
    transport = httpx.ASGITransport(app=plane.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


@pytest.mark.asyncio
async def test_a_gated_route_is_404_until_the_clients_flag_is_on(flags_for: list[UUID]) -> None:
    off, on = new_tenant("flag-off"), new_tenant("flag-on")
    for flag in GATED_FLAGS:
        _set_rule(flag, {"tenants": [str(on)], "plans": [], "percent": 0})
    async with as_admin(off) as c:
        hidden = await c.get("/v1/signals")
        unknown = await c.get("/v1/no-such-route")
        assert hidden.status_code == unknown.status_code == 404
        assert hidden.json().keys() == unknown.json().keys()
        assert hidden.json()["title"] == unknown.json()["title"]
        assert (await c.get("/v1/connectors")).status_code == 404
        assert (await c.get("/v1/crm/salesforce/writes")).status_code == 404
    async with as_admin(on) as c:
        assert (await c.get("/v1/signals")).status_code == 200
        assert (await c.get("/v1/connectors")).status_code == 200
        assert (await c.get("/v1/crm/salesforce/writes")).status_code != 404


@pytest.mark.asyncio
async def test_flags_are_deny_by_default(flags_for: list[UUID]) -> None:
    tenant = new_tenant("flag-default")
    for flag in GATED_FLAGS:
        _set_rule(flag, {"tenants": [], "plans": [], "percent": 0})
    async with as_admin(tenant) as c:
        assert (await c.get("/v1/subscriptions")).status_code == 404


def test_every_route_of_the_gated_modules_carries_its_flag() -> None:
    from account_intelligence.api.routes import connectors, crm, signals

    for module, flag in ((connectors, "connectors"), (crm, "crm"), (signals, "monitoring")):
        endpoints = [getattr(r, "endpoint", None) for r in module.router.routes]
        assert endpoints and all(endpoints), module.__name__
        assert {flag_of(fn) for fn in endpoints} == {flag}, module.__name__


def test_the_decorator_keeps_the_endpoint_signature_for_fastapi() -> None:
    async def endpoint(account: str, limit: int = 5) -> dict[str, int]:
        return {"limit": limit}

    guarded = behind_flag("monitoring")(endpoint)
    params = inspect.signature(guarded).parameters
    assert list(params)[:2] == ["account", "limit"]
    assert any(p.annotation is Request for p in params.values())
    assert flag_of(guarded) == "monitoring"
    with pytest.raises(ValueError):
        behind_flag("not_a_gated_flag")

    async def with_request(request: Request, account: str) -> dict[str, str]:
        return {"account": account}

    reused = inspect.signature(behind_flag("crm")(with_request)).parameters
    # FastAPI hands the Request to one parameter only: the endpoint's own is reused, none added
    assert [p.annotation for p in reused.values()].count(Request) == 1
    assert list(reused) == ["request", "account"]
