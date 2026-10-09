"""WS3 audit on PostgreSQL (AU1, AU2). SYNTHETIC tenants only.

Lying scenarios: a writer racing another on the chain head, a row deleted or edited behind the
application's back, a truncated tail, a read route that forgets its audit row, an audit store
that fails during a read or an admin action, an export that would carry a provider name.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.api.policy import ROUTE_POLICIES
from account_intelligence.db.session import tenant_txn
from audit import chain, events
from audit.events import AuditEvent, record
from tests.conftest import ADMIN_DSN, APP_DSN
from tests.phase3.conftest import (
    Browser,
    Plane,
    add_member,
    add_oidc_connection,
    new_tenant,
    uid,
)

# asyncio is marked per coroutine test: a module-wide mark also tags the sync tests (warning)
pytestmark = [pytest.mark.requires_postgres]
ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def _ev(i: int, **over: Any) -> AuditEvent:
    base: dict[str, Any] = {
        "at": NOW + timedelta(seconds=i),
        "actor": f"idp:user{i % 3}",
        "actor_kind": "user",
        "audit_class": "read",
        "action": "read.runs",
        "resource_type": "run",
        "resource_id": str(uuid4()),
        "route": "/v1/runs/{run_id}",
    }
    base.update(over)
    return AuditEvent(**base)


async def _write(tenant: UUID, i: int) -> int | None:
    async with tenant_txn(tenant) as s:
        seq = await record(s, _ev(i))
        await asyncio.sleep(0.01)
    return seq


async def _verify(tenant: UUID) -> chain.ChainCheck:
    async with tenant_txn(tenant) as s:
        return await chain.verify(s, NOW, batch=4, max_batches=50)


@pytest.mark.asyncio
async def test_audit_chain_is_gapless_and_serialised_under_concurrency() -> None:
    t = new_tenant("audit-race")
    seqs = await asyncio.gather(*(_write(t, i) for i in range(20)))
    assert sorted(s for s in seqs if s is not None) == list(range(1, 21))
    result = await _verify(t)
    assert result.ok and result.to_seq == 20 and result.exit_reason == "complete"
    # app_rw can neither edit nor delete an audit row
    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute("SELECT set_config('app.tenant_id', %s, true)", (str(t),))
        for sql in ("UPDATE audit_events SET actor = 'x'", "DELETE FROM audit_events"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
                c.execute(sql)


@pytest.mark.parametrize(
    ("tamper", "reason", "bad_seq"),
    [
        ("DELETE FROM audit_events WHERE tenant_id = %s AND seq = 4", "seq_gap", 4),
        (
            "UPDATE audit_events SET actor = 'idp:mallory' WHERE tenant_id = %s AND seq = 5",
            "hash_mismatch",
            5,
        ),
        (
            "UPDATE audit_events SET prev_hash = repeat('1', 64) WHERE tenant_id = %s AND seq = 6",
            "broken_link",
            6,
        ),
        ("DELETE FROM audit_events WHERE tenant_id = %s AND seq > 8", "truncated", 9),
    ],
)
@pytest.mark.asyncio
async def test_audit_chain_detects_deleted_edited_and_truncated_rows(
    tamper: str, reason: str, bad_seq: int
) -> None:
    t = new_tenant("audit-tamper")
    for i in range(10):
        await _write(t, i)
    assert (await _verify(t)).ok  # a recorded good check up to seq 10
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:  # behind the application's back
        c.execute(tamper, (t,))
    result = await _verify(t)
    assert (result.ok, result.reason, result.first_bad_seq) == (False, reason, bad_seq)
    with psycopg.connect(ADMIN_DSN) as c:
        assert c.execute(
            "SELECT ok, reason FROM audit_chain_checks WHERE tenant_id = %s ORDER BY id DESC"
            " LIMIT 1",
            (t,),
        ).fetchone() == (False, reason)


def test_events_hold_identifiers_only() -> None:
    for bad in (
        _ev(1, action="Read Runs"),
        _ev(1, resource_type="Run!"),
        _ev(1, resource_id="contains a sentence of content"),
        _ev(1, actor="two words"),
        _ev(1, route="not-a-template"),
    ):
        with pytest.raises(events.AuditRefused):
            events.validate(bad)
    a, b = _ev(1), _ev(1)
    assert (
        events.dedupe_key(a, 60)
        == events.dedupe_key(AuditEvent(**{**a.__dict__, "at": a.at + timedelta(seconds=1)}), 60)
        or a.at.second == 59
    )
    assert events.chain_hash(events.GENESIS, a, 1) != events.chain_hash(events.GENESIS, b, 1)


async def _admin_browser(plane: Plane, browser: Browser, name: str) -> tuple[UUID, str]:
    t = new_tenant(name)
    slug = f"acme-{uid()}"
    add_oidc_connection(t, plane, slug)
    add_member(t, f"{slug}:root", "admin")
    done = await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    assert done.status_code == 303
    return t, f"{slug}:root"


def _audit_rows(tenant: UUID) -> list[tuple[Any, ...]]:
    with psycopg.connect(ADMIN_DSN) as c:
        return c.execute(
            "SELECT route, action, actor, resource_id, audit_class, outcome FROM audit_events"
            " WHERE tenant_id = %s ORDER BY seq",
            (tenant,),
        ).fetchall()


@pytest.mark.asyncio
async def test_every_read_route_emits_an_audit_event(plane: Plane, browser: Browser) -> None:
    t, actor = await _admin_browser(plane, browser, "audit-reads")
    reads = sorted(
        path
        for (method, path), p in ROUTE_POLICIES.items()
        if method == "GET" and p.audit == "read" and path.startswith("/v1/")
    )
    assert len(reads) >= 6
    for template in reads:
        path = re.sub(r"\{\w+\}", "00000000-0000-4000-8000-0000000000aa", template)
        await browser.client.get(path)
    recorded = {(r[0], r[2]) for r in _audit_rows(t) if r[4] == "read"}
    missing = [tp for tp in reads if (tp, actor) not in recorded]
    assert not missing, missing


@pytest.mark.asyncio
async def test_reads_are_deduplicated_within_the_window(plane: Plane, browser: Browser) -> None:
    t, _ = await _admin_browser(plane, browser, "audit-dedupe")
    for _ in range(3):
        assert (await browser.client.get("/v1/settings")).status_code == 200
    assert sum(1 for r in _audit_rows(t) if r[0] == "/v1/settings") == 1
    plane.clock.advance(seconds=61)
    await browser.client.get("/v1/settings")
    assert sum(1 for r in _audit_rows(t) if r[0] == "/v1/settings") == 2


@pytest.mark.asyncio
async def test_read_audit_failure_refuses_the_read(
    plane: Plane, browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin_browser(plane, browser, "audit-readfail")

    async def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("audit store down")

    monkeypatch.setattr(events, "record", broken)
    reply = await browser.client.get("/v1/settings")
    assert reply.status_code == 503 and "platform_lanes" not in reply.text


@pytest.mark.asyncio
async def test_admin_actions_fail_closed_when_the_audit_write_fails(
    plane: Plane, browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    t, _ = await _admin_browser(plane, browser, "audit-adminfail")
    import account_intelligence.api.audit_log as audit_log

    async def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("audit store down")

    monkeypatch.setattr(audit_log, "record", broken)
    body = {
        "expected_version": 0,
        "settings": {"platform_lanes": ["sap"], "people_scope": {"levels_in_scope": ["VP"]}},
    }
    assert (await browser.call("PUT", "/v1/settings", json=body)).status_code >= 500
    added = await browser.call("POST", "/v1/users", json={"user_id": "x:new", "role": "viewer"})
    assert added.status_code >= 500
    with psycopg.connect(ADMIN_DSN) as c:  # neither action survived its failed audit write
        assert (
            c.execute(
                "SELECT count(*) FROM tenant_settings_versions WHERE tenant_id = %s", (t,)
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT count(*) FROM memberships WHERE tenant_id = %s AND user_id = 'x:new'", (t,)
            ).fetchone()[0]
            == 0
        )


def _endpoints() -> dict[tuple[str, str], Any]:
    """Every (method, full path) -> endpoint, from each route module's own router (routers are
    included lazily, so the assembled routers do not list their routes). Every module of the
    routes package is walked, so a new module cannot escape the sweep."""
    import importlib
    import pkgutil

    import account_intelligence.api.routes as pkg

    out: dict[tuple[str, str], Any] = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        module = importlib.import_module(f"{pkg.__name__}.{info.name}")
        for attr in ("router", "public_router", "branding_router"):
            router = getattr(module, attr, None)
            for rt in getattr(router, "routes", []):
                if not str(rt.path).startswith("/"):
                    continue
                prefix = (
                    ""
                    if str(rt.path).startswith(("/operator", "/tasks", "/healthz", "/readyz"))
                    else "/v1"
                )
                for m in getattr(rt, "methods", set()):
                    out[(m, prefix + rt.path)] = rt.endpoint
    return out


def test_run_export_and_admin_handlers_record_their_audit_event() -> None:
    endpoints = _endpoints()
    missing = []
    for (method, path), policy in ROUTE_POLICIES.items():
        if policy.audit not in ("run", "export", "admin") or not path.startswith("/v1/"):
            continue
        source = textwrap.dedent(inspect.getsource(endpoints[(method, path)]))
        calls = {
            n.func.id
            for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        if not calls & {"audit_action", "audited_txn"}:
            missing.append((method, path))
    assert not missing, missing


@pytest.mark.asyncio
async def test_audit_export_is_client_safe_and_tenant_scoped(
    plane: Plane, browser: Browser, tmp_path: Path
) -> None:
    t, actor = await _admin_browser(plane, browser, "audit-export")
    other = new_tenant("audit-export-other")
    run = uuid4()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        for tenant, op in ((t, "people_by_title"), (t, "web_search"), (other, "web_search")):
            c.execute(
                "INSERT INTO tool_call_events (tenant_id, run_id, args_hash, attempt, event,"
                " owner, lease_epoch, provider_code, operation, charged_usd, credits,"
                " created_at) VALUES (%s, %s, %s, 1, 'done', 'w', 1, 'p_people', %s, 1.25,"
                " 42, now())",
                (tenant, run, uuid4().hex, op),
            )
    async with tenant_txn(t) as s:  # a hostile identifier must reach the CSV as inert text
        await record(s, _ev(1, resource_id="=cmd", actor="idp:x"))
    async with tenant_txn(other) as s:
        await record(s, _ev(2, actor="idp:other-tenant-user"))
    reply = await browser.client.get("/v1/audit/export")
    assert reply.status_code == 200 and reply.headers["content-type"].startswith("text/csv")
    body = reply.text
    assert "people_search" in body and "web_search" in body
    assert "p_people" not in body and "1.25" not in body and "42" not in body.split(",")
    assert "idp:other-tenant-user" not in body
    assert "'=cmd" in body and ",=cmd" not in body
    out = tmp_path / "audit.csv"
    out.write_text(body)
    scan = subprocess.run(
        [
            sys.executable,
            str(
                ROOT / ".claude/skills/account-intelligence-platform/scripts/scan_client_output.py"
            ),
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert scan.returncode == 0, scan.stdout + scan.stderr
    # the export recorded itself; the listing is the caller's tenant only
    assert any(r[1] == "export.audit_csv" and r[2] == actor for r in _audit_rows(t))
    listed = (await browser.client.get("/v1/audit", params={"limit": 200})).json()["events"]
    assert listed and all(e["actor"] != "idp:other-tenant-user" for e in listed)
