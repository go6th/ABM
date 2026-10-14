"""Shared fixtures for the replica tests on PostgreSQL: a fresh demo world per test module (built
with the dev seed's own code, SYNTHETIC data only) and a real application with the identity set."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _seed_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "seed_replica_demo", ROOT / "scripts" / "seed_replica_demo.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    import os

    from tests.conftest import ADMIN_DSN

    seed = _seed_module()
    payload_dir = tmp_path_factory.mktemp("payloads")
    os.environ["PAYLOAD_DIR"] = str(payload_dir)
    now = datetime.now(UTC)
    with psycopg.connect(ADMIN_DSN) as conn:
        s = seed.Seeder(conn, now)
        a = s.tenant(f"Replica A {uuid4().hex[:6]}")
        s.client_settings(a, pool=2000, cap=34)
        for builder in (seed.highmark, seed.cambia, seed.cigna, seed.medica):
            s.account(a, "priya-test", builder())
        b = s.tenant(f"Replica B {uuid4().hex[:6]}")
        s.client_settings(b, pool=3000, cap=10)
        s.account(b, "other-test", seed.cigna())
        conn.commit()
    return {"a": a, "b": b, "payloads": payload_dir}


def _plane(tenant: UUID, user: str, payload_dir: Path) -> Any:
    from account_intelligence.api.auth import Identity, identity
    from account_intelligence.platform.hermetic import LocalPayloadStore
    from tests.phase3.conftest import add_member, make_app

    add_member(tenant, user, "analyst")
    plane = make_app()
    plane.app.state.container.payloads = LocalPayloadStore(payload_dir)
    now = datetime.now(UTC)

    async def ident() -> Identity:
        return Identity(user, tenant, "analyst", now)

    plane.app.dependency_overrides[identity] = ident
    return plane


async def _call(plane: Any, method: str, path: str, **kw: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=plane.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://app.test") as c:
        return await c.request(method, path, **kw)
