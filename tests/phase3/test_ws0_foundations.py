"""WS0 foundations: the route catalog is enforced at runtime (deny by default), every route's
template is resolved by the guard, tenant storage paths, download tokens and cache keys are bound
to one tenant, and unconfigured ports fail closed. SYNTHETIC."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import APIRouter, Depends
from hypothesis import given
from hypothesis import strategies as st

from account_intelligence.api import guard
from account_intelligence.api.guard import route_guard
from account_intelligence.api.main import create_app
from account_intelligence.api.policy import ROUTE_POLICIES, RoutePolicy, register
from account_intelligence.platform.ports import NotConfigured
from tenancy.keys import cache_key, tenant_of_cache_key
from tenancy.ports import UnconfiguredMailer, UnconfiguredObjectStore, UnconfiguredSecretStore
from tenancy.storage import (
    PathRefused,
    require_owned,
    sign_download,
    tenant_of_key,
    tenant_path,
    tenant_prefix,
    verify_download,
)

A = UUID("aaaaaaaa-0000-4000-8000-000000000001")
B = UUID("bbbbbbbb-0000-4000-8000-000000000002")
NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
SECRET = b"s" * 32


async def _call(app: Any, method: str, path: str, **kw: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, path, **kw)


def test_unregistered_route_is_refused_by_the_guard() -> None:
    app = create_app()
    extra = APIRouter(prefix="/v1", dependencies=[Depends(route_guard)])

    @extra.get("/ws0-unregistered")
    async def unregistered() -> dict[str, str]:
        return {"leak": "should never be served"}

    app.include_router(extra)
    reply = asyncio.run(_call(app, "GET", "/v1/ws0-unregistered"))
    assert reply.status_code == 403
    assert "should never be served" not in reply.text


def test_guard_resolves_every_route_template(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard must see the full template of every /v1 and /operator route (a wrong template
    would make a registered route unreachable or, worse, match another route's policy)."""
    seen: list[tuple[str, str]] = []

    def record(method: str, path: str) -> None:
        seen.append((method, path))
        return None

    monkeypatch.setattr(guard, "policy_for", record)
    app = create_app()
    routes = [
        (m.upper(), p)
        for p, ops in app.openapi()["paths"].items()
        if p.startswith(("/v1/", "/operator/"))
        for m in ops
        if m not in ("parameters", "head")
    ]
    for method, template in routes:
        path = re.sub(r"\{\w+\}", str(uuid4()), template)
        reply = asyncio.run(_call(app, method, path, json={}))
        assert reply.status_code == 403, (method, template)
    assert sorted(seen) == sorted(routes)


def test_route_catalog_check_passes() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import check_route_catalog

    assert check_route_catalog.problems() == []


def test_a_policy_cannot_be_registered_twice() -> None:
    key = next(iter(ROUTE_POLICIES))
    with pytest.raises(ValueError):
        register(key[0], key[1], RoutePolicy(frozenset(), frozenset(), "none", "none"))


# ---------------------------------------------------------------- storage (TN4)

_SEG = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._=-]{0,20}", fullmatch=True).filter(
    lambda s: s not in (".", "..")
)


@given(
    st.uuids(),
    st.sampled_from(["raw", "branding", "exports", "audit-exports"]),
    st.lists(_SEG, min_size=1, max_size=4),
)
def test_storage_paths_and_tokens_are_tenant_bound(t: UUID, kind: str, parts: list[str]) -> None:
    key = tenant_path(t, kind, *parts)
    assert key.startswith(tenant_prefix(t))
    assert tenant_of_key(key) == t
    assert require_owned(t, key) == key
    other = UUID(int=(t.int + 1) % (1 << 128))
    with pytest.raises(PathRefused):
        require_owned(other, key)


@pytest.mark.parametrize("bad", ["", "..", ".", "a/b", "../x", "a\\b", "x" * 201, "-lead"])
def test_storage_segments_cannot_escape_the_prefix(bad: str) -> None:
    with pytest.raises(PathRefused):
        tenant_path(A, "exports", bad)


def test_storage_refuses_unknown_kinds_and_empty_keys() -> None:
    with pytest.raises(PathRefused):
        tenant_path(A, "elsewhere", "x")
    with pytest.raises(PathRefused):
        tenant_path(A, "exports")
    assert tenant_of_key(f"tenants/{A}/../{B}/x") is None
    assert tenant_of_key(f"tenants/{A}//x") is None
    assert tenant_of_key("public/cache/x") is None


def test_download_tokens_bind_tenant_key_hash_and_expiry() -> None:
    key = tenant_path(A, "exports", "run", "a" * 64 + ".csv")
    token = sign_download(SECRET, A, key, "b" * 64, NOW, timedelta(minutes=5))
    grant = verify_download(SECRET, token, A, NOW + timedelta(minutes=4))
    assert (grant.tenant_id, grant.key, grant.sha256) == (A, key, "b" * 64)
    for session, at, secret in (
        (B, NOW, SECRET),  # another tenant's session
        (A, NOW + timedelta(minutes=5), SECRET),  # expired
        (A, NOW, b"x" * 32),  # wrong key
    ):
        with pytest.raises(PathRefused):
            verify_download(secret, token, session, at)
    body, mac = token.split(".")
    forged = body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + mac
    for bad in (forged, "garbage", "a.b.c", ""):
        with pytest.raises(PathRefused):
            verify_download(SECRET, bad, A, NOW)
    with pytest.raises(PathRefused):
        sign_download(SECRET, A, key, "not-a-hash", NOW, timedelta(minutes=5))
    with pytest.raises(PathRefused):
        sign_download(SECRET, A, key, "b" * 64, NOW, timedelta(0))


def test_cache_keys_carry_the_tenant_prefix() -> None:
    ka = cache_key(A, "membership", "user-1")
    assert ka == f"t:{A}:membership:user-1"
    assert tenant_of_cache_key(ka) == A
    assert cache_key(B, "membership", "user-1") != ka
    for bad in (("membership", ""), ("membership", "a:b"), ("unregistered", "x")):
        with pytest.raises(ValueError):
            cache_key(A, *bad)
    assert tenant_of_cache_key("x:y") is None
    assert tenant_of_cache_key("t:not-a-uuid:membership") is None


def test_unconfigured_ports_fail_closed() -> None:
    async def all_calls() -> None:
        for call in (
            UnconfiguredSecretStore().put("n", b"v"),
            UnconfiguredSecretStore().read("r"),
            UnconfiguredSecretStore().destroy("r"),
            UnconfiguredObjectStore().put_if_absent("k", b"v", "text/plain"),
            UnconfiguredObjectStore().get("k"),
            UnconfiguredObjectStore().list_prefix("p", 1),
            UnconfiguredMailer().send("a@example.com", "t", {}),
        ):
            with pytest.raises(NotConfigured):
                await call

    asyncio.run(all_calls())
