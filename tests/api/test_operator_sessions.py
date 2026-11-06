"""Opaque staff sessions (fix prompt B, task 3; ADR-0111). The operator console's cookie used to
hold the raw Google ID token; now the console trades a verified staff IdP token (fresh MFA) once
for a random 256-bit session token. Only its SHA-256 is stored (``operator_sessions``, staff
plane, no tenant). It ends after 30 minutes idle or 8 hours absolute (``config/session.yaml``,
the customer session rules), or at sign-out; a session token can never mint another session, so
the absolute limit holds. Every staff route's 10-minute MFA step-up rule still applies on top.
SYNTHETIC staff only."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
import psycopg
import pytest

from tests.conftest import ADMIN_DSN
from tests.fakes.phase3 import FakeStaffIdp
from tests.phase3.conftest import BASE, Plane, make_app

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

OPERATOR = {"Authorization": "Bearer operator:op-session"}
OPERATOR_NO_MFA = {"Authorization": "Bearer operator:op-session:no-mfa"}


def staff_plane() -> Plane:
    plane = make_app()
    plane.app.state.container.tenant_plane.staff_verifier = FakeStaffIdp(now=plane.clock.now())
    return plane


@asynccontextmanager
async def client(plane: Plane) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=plane.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _mint(c: httpx.AsyncClient) -> str:
    r = await c.post("/operator/v1/sessions", headers=OPERATOR)
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    assert isinstance(token, str) and token.startswith("ops_")
    return token


async def test_a_fresh_staff_sign_in_mints_an_opaque_session() -> None:
    plane = staff_plane()
    async with client(plane) as c:
        assert (await c.post("/operator/v1/sessions")).status_code == 401
        assert (await c.post("/operator/v1/sessions", headers=OPERATOR_NO_MFA)).status_code == 403
        r = await c.post("/operator/v1/sessions", headers=OPERATOR)
        assert r.status_code == 201, r.text
        body = r.json()
        token = body["token"]
        assert len(token) >= 4 + 43  # "ops_" + 256 random bits, url-safe base64
        expires = datetime.fromisoformat(body["expires_at"])
        assert (expires - plane.clock.now()).total_seconds() == 8 * 3600
        assert body["idle_timeout_s"] == 1800
        # the session token is a bearer for the staff routes, as the IdP token was
        who = await c.get("/operator/v1/session", headers=bearer(token))
        assert who.status_code == 200, who.text
        assert who.json() == {"staff_id": "op-session", "role": "operator", "mfa_fresh": True}
    with psycopg.connect(ADMIN_DSN) as conn:
        rows = conn.execute(
            "SELECT token_hash, staff_id, role FROM operator_sessions WHERE staff_id = %s",
            ("op-session",),
        ).fetchall()
    digest = hashlib.sha256(token.encode()).digest()
    assert (bytes(rows[-1][0]), rows[-1][1], rows[-1][2]) == (digest, "op-session", "operator")
    assert all(token.encode() not in bytes(r[0]) for r in rows)  # never the token itself


async def test_a_session_token_cannot_mint_another_session() -> None:
    async with client(staff_plane()) as c:
        token = await _mint(c)
        again = await c.post("/operator/v1/sessions", headers=bearer(token))
        assert again.status_code == 403


async def test_an_unknown_or_forged_session_token_is_refused() -> None:
    async with client(staff_plane()) as c:
        for forged in ("ops_" + "A" * 43, "ops_", "ops_short"):
            assert (await c.get("/operator/v1/session", headers=bearer(forged))).status_code == 401


async def test_a_session_ends_after_30_minutes_idle() -> None:
    plane = staff_plane()
    async with client(plane) as c:
        token = await _mint(c)
        plane.clock.advance(seconds=1799)
        # still alive: the session is accepted and the route answers 403 only because its own
        # 10-minute MFA step-up has passed (a dead session is 401)
        assert (await c.get("/operator/v1/session", headers=bearer(token))).status_code == 403
        plane.clock.advance(seconds=1801)
        assert (await c.get("/operator/v1/session", headers=bearer(token))).status_code == 401


async def test_a_session_ends_8_hours_after_sign_in_however_active() -> None:
    plane = staff_plane()
    async with client(plane) as c:
        token = await _mint(c)
        for _ in range(16):  # 16 x 29 minutes = 7 h 44 min, active all along
            plane.clock.advance(seconds=29 * 60)
            alive = await c.get("/operator/v1/session", headers=bearer(token))
            assert alive.status_code == 403  # accepted; only the step-up is stale
        plane.clock.advance(seconds=17 * 60)  # 8 h 01 min after sign-in
        assert (await c.get("/operator/v1/session", headers=bearer(token))).status_code == 401


async def test_sign_out_revokes_the_session() -> None:
    async with client(staff_plane()) as c:
        token = await _mint(c)
        out = await c.delete("/operator/v1/sessions/current", headers=bearer(token))
        assert out.status_code == 204
        assert (await c.get("/operator/v1/session", headers=bearer(token))).status_code == 401
        # signing out an IdP token (no session) is refused, not silently accepted
        assert (
            await c.delete("/operator/v1/sessions/current", headers=OPERATOR)
        ).status_code == 400
