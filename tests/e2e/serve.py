"""The local end-to-end API for the Playwright suite (``web/e2e``). SYNTHETIC only.

The real application (routes, guard, RLS on the test database) over the faked tenant plane, plus
a fake OIDC IdP reachable over HTTP at ``/fake-idp`` (mounted on THIS test server only, never in
the application): ``POST /fake-idp/next`` {subject, groups} picks who signs in next and
``GET /fake-idp/authorize`` answers like the IdP. The connection keeps its https IdP address (the
database refuses anything else); the Playwright suite routes that navigation here.

Seeds two tenants: ``e2e-a`` (members ada/admin, ana/analyst, sam/seller, val/viewer, accounts
``acme-a.example`` and ``acme-b.example``; tenant policy auto-approves runs within the reserve) and
``e2e-b`` (bob/admin, account
``beta-only.example``). Prints ``E2E READY`` when listening.

``POST /fake-idp/drive`` {run_id} runs a run created through the UI to its end over the SYNTHETIC
world of the system tests (scripted providers for ``acme.example``, scripted model): the real
slice lifecycle, connectors, ledger, grading and tracker, never a real provider or model.

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

    def tenant(
        slug: str,
        members: dict[str, str],
        domains: list[str],
        *,
        abm_enabled: bool = False,
    ) -> UUID:
        t = new_tenant(f"e2e-{slug}")
        import json

        import psycopg

        from tests.conftest import ADMIN_DSN

        with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
            c.execute(
                "UPDATE tenants SET policy_json = %s WHERE id = %s",
                (json.dumps({"auto_approve_within_reserve": True}), t),
            )
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
            if abm_enabled:
                # The production setup agent requires a pursuing client. Seed one through the
                # same validated, versioned tenant-settings shape the admin UI writes so the
                # E2E opt-in exercises that real product rule.
                from tenancy.settings import canonical, default_settings, settings_hash

                configured = default_settings().model_copy(
                    update={"pursuing_client": "Synthetic Partner Co"}
                )
                c.execute(
                    "INSERT INTO tenant_settings_versions"
                    " (tenant_id, version, settings_json, settings_hash, created_by, created_at)"
                    " VALUES (%s, 1, %s::jsonb, %s, 'synthetic-e2e', now())",
                    (t, canonical(configured).decode(), settings_hash(configured)),
                )
                version = c.execute(
                    "SELECT coalesce(max(version), 0) + 1 FROM feature_flags"
                    " WHERE flag = 'abm_agents'"
                ).fetchone()[0]
                c.execute(
                    "INSERT INTO feature_flags (flag, version, rule, created_by, created_at)"
                    " VALUES ('abm_agents', %s, %s, 'synthetic-e2e', now())",
                    (version, json.dumps({"tenants": [str(t)]})),
                )
        return t

    suffix = os.urandom(3).hex()
    os.environ["E2E_SLUG_A"], os.environ["E2E_SLUG_B"] = f"e2e-a-{suffix}", f"e2e-b-{suffix}"
    seeded = {
        "a": tenant(
            os.environ["E2E_SLUG_A"],
            MEMBERS_A,
            ["acme-a.example", "acme-b.example"],
            abm_enabled=True,
        ),
        "b": tenant(os.environ["E2E_SLUG_B"], {"bob": "admin"}, ["beta-only.example"]),
    }
    _seed_alert(seeded["a"], f"{os.environ['E2E_SLUG_A']}:ada")
    return seeded


def _seed_alert(tenant: UUID, user: str) -> None:
    """One alert for tenant A's admin, built by the real engine (Phase 4 flows)."""
    import asyncio

    import psycopg

    from account_intelligence.db.session import engine
    from tests.conftest import ADMIN_DSN
    from tests.phase4.test_ws5_alerting import evaluate, make_event

    with psycopg.connect(ADMIN_DSN) as c:
        account = c.execute(
            "SELECT id FROM accounts WHERE tenant_id = %s AND domain = 'acme-a.example'", (tenant,)
        ).fetchone()[0]

    async def go() -> None:
        try:
            ev = await make_event(
                tenant, account, signal="build_role_posting", title="Platform owner role posted"
            )
            await evaluate(tenant, ev, users=(user,))
        finally:
            # This seeding helper owns a temporary asyncio.run loop. Do not leave an asyncpg
            # connection pooled for the different Uvicorn loop that serves the browser suite.
            await engine.dispose()

    asyncio.run(go())


def build() -> Any:
    """The application and its fake IdP routes."""
    from datetime import UTC, datetime

    from tests.phase3.conftest import make_app

    plane = make_app()
    plane.clock.current = datetime.now(UTC)
    plane.app.state.container.tenant_plane.base_url = WEB_ORIGIN
    # the synthetic plan's cap proposal (about $9.75) fits a $20 run budget, so the tenant policy
    # auto-approves it like the system tests (tests/system/world.py); no approval UI is needed
    from dataclasses import replace
    from decimal import Decimal

    container = plane.app.state.container
    container.limits = replace(container.limits, budget_usd=Decimal("20.00"))
    tenants = _seed(plane)
    nxt: dict[str, Any] = {"subject": "ada", "groups": ["admins"]}
    app = plane.app
    from account_intelligence.chat.registry import get_chat_registry
    from account_intelligence.chat.service import ChatEvent, ChatService, ChatUsage

    class SyntheticChatProvider:
        async def stream(self, model: Any, messages: Any, max_output_tokens: int) -> Any:
            """A deterministic stream for browser coverage; never contacts a model provider."""
            del model, messages, max_output_tokens
            yield ChatEvent("text", text="Synthetic response")
            yield ChatEvent("usage", usage=ChatUsage(input_tokens=24, output_tokens=4))

    app.state.chat_service = ChatService(get_chat_registry(), SyntheticChatProvider())

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

    @app.post("/fake-idp/drive")
    async def drive_run(request: Request) -> JSONResponse:
        """Run one UI-created run to its end in the synthetic world (see the module docstring)."""
        import psycopg

        from agents.production.wiring import AbmGraphFactory
        from tests.conftest import ADMIN_DSN
        from tests.connectors.conftest import FakeClock, FakeProvider, FakeSleep, Harness
        from tests.system.test_abm_production import AbmScript
        from tests.system.world import drive

        run_id = UUID((await request.json())["run_id"])
        with psycopg.connect(ADMIN_DSN) as c:
            row = c.execute("SELECT tenant_id FROM account_runs WHERE id = %s", (run_id,))
            tenant_id = row.fetchone()[0]
        from account_intelligence.connectors.base import ProviderCode

        clock = FakeClock()
        harness = Harness(
            clock=clock,
            sleep=FakeSleep(clock),
            providers={code: FakeProvider(clock) for code in ProviderCode},
            # the API's own store: exports read what the run wrote
            payloads=plane.app.state.container.payloads,
            resolver_table={},
        )
        # Keep the run's builder from the real application container: synthetic providers and
        # scripted model drive the run, then the production export reader, validator and file
        # store build the same deliverables the UI downloads.
        await drive(
            harness,
            AbmScript(),
            tenant_id,
            run_id,
            builder=plane.app.state.container.builder,
            graph_factory=AbmGraphFactory(),
        )
        builder = plane.app.state.container.builder
        if builder is None or builder.reader is None:
            build_errors = ["builder_not_configured"]
        else:
            try:
                content = await builder.reader.read(str(tenant_id), str(run_id))
                assembled = builder.assemble(content)
                build_errors = sorted({error.code for error in assembled.errors})
            except Exception as error:
                # The synthetic harness reports only the fixed error type, never payloads or SQL.
                build_errors = [type(error).__name__]
        with psycopg.connect(ADMIN_DSN) as c:
            run = c.execute(
                "SELECT status, finished_reason FROM account_runs WHERE id = %s", (run_id,)
            ).fetchone()
        return JSONResponse(
            {
                "status": run[0],
                "finished_reason": run[1],
                "build_error_codes": build_errors,
            }
        )

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
    port = int(os.environ.get("E2E_API_PORT", "8088"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    return 0


if __name__ == "__main__":
    sys.exit(main())
