"""Phase 3 fixtures: an application with a fully faked tenant plane, synthetic tenants with IdP
connections, and a browser-like client that signs in through the fake IdPs. SYNTHETIC only."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio

from tests.conftest import ADMIN_DSN
from tests.fakes.idp import FakeOidcIdp, FakeSamlIdp
from tests.fakes.phase3 import FakeClock, FakeMailer, FakeObjectStore, FakeScanner, FakeSecretStore

BASE = "https://app.test"
GROUPS = {"sales": "seller", "analysts": "analyst", "admins": "admin", "readers": "viewer"}


@dataclass
class Plane:
    """The faked collaborators of one test application."""

    app: Any
    clock: FakeClock
    oidc: FakeOidcIdp
    saml: FakeSamlIdp
    secrets: FakeSecretStore
    objects: FakeObjectStore
    mailer: FakeMailer
    scanner: FakeScanner


def make_app() -> Plane:
    """A real application (real routes, guard, database) over faked external ports."""
    from account_intelligence.api.main import create_app
    from account_intelligence.api.tenant_plane import CSRF_KEY_REF, DOWNLOAD_KEY_REF, TenantPlane
    from account_intelligence.app.container import build_container
    from identity.sessions import load_policy

    clock = FakeClock()
    idp, saml = FakeOidcIdp(), FakeSamlIdp()
    secrets_store, objects = FakeSecretStore(), FakeObjectStore()
    mailer, scanner = FakeMailer(), FakeScanner()
    secrets_store.values[CSRF_KEY_REF] = b"c" * 32
    secrets_store.values[DOWNLOAD_KEY_REF] = b"d" * 32
    container = build_container({"APP_ENV": "test"})
    container.clock = clock
    container.tenant_plane = TenantPlane(
        idp=idp,
        secrets=secrets_store,
        objects=objects,
        mailer=mailer,
        scanner=scanner,
        clock=clock,
        base_url=BASE,
        policy=load_policy(),
    )
    app = create_app()
    app.state.container = container
    app.state.draining = False
    return Plane(app, clock, idp, saml, secrets_store, objects, mailer, scanner)


def new_tenant(name: str) -> UUID:
    """A synthetic tenant (as the migrator)."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        tid: UUID = c.execute(
            "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (f"{name}-synthetic",)
        ).fetchone()[0]
        c.execute("INSERT INTO wallets (tenant_id, balance) VALUES (%s, 100)", (tid,))
    return tid


def add_oidc_connection(tenant: UUID, plane: Plane, slug: str, **over: Any) -> UUID:
    """An OIDC connection owned by ``tenant`` pointing at the fake IdP."""
    import json

    values = {
        "slug": slug,
        "issuer": plane.oidc.issuer,
        "client_id": plane.oidc.client_id,
        "authorization_endpoint": "https://idp.synthetic.test/authorize",
        "token_endpoint": "https://idp.synthetic.test/token",
        "jwks_uri": "https://idp.synthetic.test/jwks",
        "verified_domains": ["synthetic.test"],
        "group_role_map": json.dumps(GROUPS),
        "jit_enabled": False,
        "require_admin_mfa": True,
        **over,
    }
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cid: UUID = c.execute(
            "INSERT INTO idp_connections (tenant_id, slug, protocol, issuer, client_id,"
            " authorization_endpoint, token_endpoint, jwks_uri, verified_domains,"
            " group_role_map, jit_enabled, require_admin_mfa) VALUES (%(t)s, %(slug)s, 'oidc',"
            " %(issuer)s, %(client_id)s, %(authorization_endpoint)s, %(token_endpoint)s,"
            " %(jwks_uri)s, %(verified_domains)s, %(group_role_map)s, %(jit_enabled)s,"
            " %(require_admin_mfa)s) RETURNING id",
            {**values, "t": tenant},
        ).fetchone()[0]
    return cid


def add_saml_connection(tenant: UUID, plane: Plane, slug: str) -> UUID:
    """A SAML connection owned by ``tenant`` trusting the fake SAML IdP's certificate."""
    import json

    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cid: UUID = c.execute(
            "INSERT INTO idp_connections (tenant_id, slug, protocol, issuer, saml_sso_url,"
            " saml_idp_cert_pem, sp_entity_id, verified_domains, group_role_map)"
            " VALUES (%s, %s, 'saml', %s, 'https://saml-idp.synthetic.test/sso', %s,"
            " 'https://app.test/saml/sp', %s, %s) RETURNING id",
            (
                tenant,
                slug,
                plane.saml.issuer,
                plane.saml.cert,
                ["synthetic.test"],
                json.dumps(GROUPS),
            ),
        ).fetchone()[0]
    return cid


def add_member(tenant: UUID, user_id: str, role: str, status: str = "active") -> None:
    """A membership row (as the migrator)."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO memberships (tenant_id, user_id, role, status) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (tenant_id, user_id) DO UPDATE SET role = EXCLUDED.role,"
            " status = EXCLUDED.status, version = memberships.version + 1",
            (tenant, user_id, role, status),
        )


class Browser:
    """An httpx client with a cookie jar, as a browser would hold one, plus sign-in helpers."""

    def __init__(self, plane: Plane) -> None:
        self.plane = plane
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=plane.app, raise_app_exceptions=False),
            base_url=BASE,
        )
        self.csrf: str | None = None

    async def close(self) -> None:
        """Close the client."""
        await self.client.aclose()

    async def oidc_sign_in(
        self,
        slug: str,
        subject: str,
        *,
        extra: dict[str, Any] | None = None,
        return_to: str = "/",
        token: str | None = None,
    ) -> httpx.Response:
        """Start, let the fake IdP authorize, and call back; refreshes the CSRF token."""
        start = await self.client.get(f"/v1/sso/{slug}/start", params={"return_to": return_to})
        assert start.status_code == 302, start.text
        state, code = self.plane.oidc.authorize(
            start.headers["location"],
            subject=subject,
            now=self.plane.clock.now(),
            extra=extra,
            token=token,
        )
        done = await self.client.get("/v1/sso/oidc/callback", params={"state": state, "code": code})
        if done.status_code == 303:
            await self.refresh_csrf()
        return done

    async def saml_sign_in(self, slug: str, subject: str, **kw: Any) -> httpx.Response:
        """Start, let the fake SAML IdP answer, and post to the ACS."""
        from urllib.parse import parse_qs, urlsplit

        start = await self.client.get(f"/v1/sso/{slug}/start")
        assert start.status_code == 302, start.text
        q = {k: v[0] for k, v in parse_qs(urlsplit(start.headers["location"]).query).items()}
        import base64
        import zlib

        xml = zlib.decompress(base64.b64decode(q["SAMLRequest"]), -15).decode()
        request_id = xml.split('ID="', 1)[1].split('"', 1)[0]
        response = self.plane.saml.response(
            request_id=request_id,
            acs_url=f"{BASE}/v1/sso/saml/acs",
            audience="https://app.test/saml/sp",
            subject=subject,
            now=self.plane.clock.now(),
            email=f"{subject}@synthetic.test",
            **kw,
        )
        done = await self.client.post(
            "/v1/sso/saml/acs", data={"SAMLResponse": response, "RelayState": q["RelayState"]}
        )
        if done.status_code == 303:
            await self.refresh_csrf()
        return done

    async def refresh_csrf(self) -> None:
        """Read this session's CSRF token."""
        who = await self.client.get("/v1/sso/session")
        self.csrf = who.json().get("csrf_token") if who.status_code == 200 else None

    async def call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        """A request with the CSRF header on unsafe methods."""
        headers = dict(kw.pop("headers", {}) or {})
        if method.upper() not in ("GET", "HEAD") and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        return await self.client.request(method, path, headers=headers, **kw)


@pytest.fixture
def plane() -> Plane:
    return make_app()


@pytest_asyncio.fixture
async def browser(plane: Plane) -> AsyncIterator[Browser]:
    b = Browser(plane)
    yield b
    await b.close()


def utc(**kw: int) -> dt.datetime:
    """A fixed aware time."""
    return dt.datetime(2026, 3, 2, tzinfo=dt.UTC) + dt.timedelta(**kw)


def uid() -> str:
    """A unique synthetic slug suffix."""
    return uuid4().hex[:8]
