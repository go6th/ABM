"""The local end-to-end API for the Playwright suite (``web/e2e``). SYNTHETIC only.

The real application (routes, guard, RLS on the test database) over the faked tenant plane, plus
a fake OIDC IdP reachable over HTTP at ``/fake-idp`` (mounted on THIS test server only, never in
the application): ``POST /fake-idp/next`` {subject, groups} picks who signs in next and
``GET /fake-idp/authorize`` answers like the IdP. The connection keeps its https IdP address (the
database refuses anything else); the Playwright suite routes that navigation here.

Seeds two tenants: ``e2e-a`` (members ada/admin, ana/analyst, sam/seller, val/viewer, accounts
``acme-a.example`` and ``acme-b.example``) and ``e2e-b`` (bob/admin, account
``beta-only.example``). Prints ``E2E READY`` when listening.

Usage: ``TEST_DB=... python -m tests.e2e.serve`` (refuses outside a database named like a test one).
"""

from __future__ import annotations

import os
import sys
from typing import Any
from uuid import UUID

from fastapi import Request  # module level: route annotations are resolved from module globals
from fastapi.responses import JSONResponse, RedirectResponse

WEB_ORIGIN = os.environ.get("E2E_WEB_ORIGIN", "http://localhost:3000")
MEMBERS_A = {"ada": "admin", "ana": "analyst", "sam": "seller", "val": "viewer"}
GROUP_OF = {"admin": "admins", "analyst": "analysts", "seller": "sales", "viewer": "readers"}


def _seed(plane: Any) -> dict[str, UUID]:
    from tests.phase3.conftest import add_member, add_oidc_connection, new_tenant

    def tenant(slug: str, members: dict[str, str], domains: list[str]) -> UUID:
        t = new_tenant(f"e2e-{slug}")
        add_oidc_connection(
            t, plane, slug
        )  # https://idp.synthetic.test, intercepted by the browser
        for who, role in members.items():
            add_member(t, f"{slug}:{who}", role)
        import psycopg

        from tests.conftest import ADMIN_DSN

        with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
            for d in domains:
                c.execute("INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s)", (t, d))
        return t

    suffix = os.urandom(3).hex()
    os.environ["E2E_SLUG_A"], os.environ["E2E_SLUG_B"] = f"e2e-a-{suffix}", f"e2e-b-{suffix}"
    return {
        "a": tenant(os.environ["E2E_SLUG_A"], MEMBERS_A, ["acme-a.example", "acme-b.example"]),
        "b": tenant(os.environ["E2E_SLUG_B"], {"bob": "admin"}, ["beta-only.example"]),
    }


def build() -> Any:
    """The application and its fake IdP routes."""
    from datetime import UTC, datetime

    from tests.phase3.conftest import make_app

    plane = make_app()
    plane.clock.current = datetime.now(UTC)
    plane.app.state.container.tenant_plane.base_url = WEB_ORIGIN
    tenants = _seed(plane)
    nxt: dict[str, Any] = {"subject": "ada", "groups": ["admins"]}
    app = plane.app

    @app.post("/fake-idp/next")
    async def choose(request: Request) -> JSONResponse:
        nxt.update(await request.json())
        return JSONResponse({"ok": True})

    @app.get("/fake-idp/authorize")
    async def authorize(request: Request) -> RedirectResponse:
        plane.clock.current = datetime.now(UTC)
        url = str(request.url)
        state, code = plane.oidc.authorize(
            url, subject=nxt["subject"], now=plane.clock.now(), extra={"groups": nxt["groups"]}
        )
        redirect = request.query_params["redirect_uri"]
        return RedirectResponse(f"{redirect}?state={state}&code={code}", status_code=302)

    @app.get("/fake-idp/tenants")
    async def seeded() -> JSONResponse:
        return JSONResponse(
            {
                "slug_a": os.environ["E2E_SLUG_A"],
                "slug_b": os.environ["E2E_SLUG_B"],
                **{k: str(v) for k, v in tenants.items()},
            }
        )

    return app


def main() -> int:
    """Serve on 127.0.0.1:8080."""
    db = os.environ.get("TEST_DB", "")
    if not (db.startswith("ai_") or "test" in db):
        print("serve: refused (TEST_DB must name a test database)", file=sys.stderr)
        return 2
    import uvicorn

    app = build()
    print("E2E READY", flush=True)
    # lifespan off: the lifespan builds the production container, which would replace the fakes
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning", lifespan="off")
    return 0


if __name__ == "__main__":
    sys.exit(main())
