"""WS6 client history connectors (CN1 to CN4, D-65). SYNTHETIC sources only.

Lying scenarios: a tampered or replayed consent state, a consent finished by another admin, a
source that grants wider scopes (at consent or on refresh), a revoked grant, a CRM that returns
fields nobody asked for, a fetch that would write, a token that would end up in a log, an audit
row or a response, connector content that would reach a client table.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any
from uuid import UUID

import psycopg
import pytest

from connectors_client import ports, scopes
from tests.conftest import ADMIN_DSN
from tests.fakes.connectors import TOKEN_CANARY, FakeCrm, FakeOAuth
from tests.fakes.flags import enable_flags
from tests.phase3.conftest import (
    Browser,
    Plane,
    add_member,
    add_oidc_connection,
    new_tenant,
    uid,
)


def test_scopes_are_exactly_the_table_and_crm_fields_are_allow_listed() -> None:
    crm_scopes = scopes.spec("salesforce").scopes
    assert scopes.scopes_ok("salesforce", tuple(crm_scopes))
    assert scopes.scopes_ok("salesforce", (*crm_scopes, "openid"))  # a sign-in scope is ignored
    assert not scopes.scopes_ok("salesforce", (*crm_scopes, "full"))
    assert not scopes.scopes_ok("salesforce", ())
    assert scopes.allowed_fields(
        "salesforce", "Account", {"Account"}, {"Account.Name", "Account.Secret"}
    ) == ("Name",)
    assert scopes.allowed_fields("salesforce", "Account", set(), {"Account.Name"}) == ()
    assert scopes.allowed_fields("salesforce", "Lead", {"Lead"}, {"Lead.Email"}) == ()
    # call notes stay off (D-65)
    assert not scopes.spec("call_notes").enabled


def test_connector_ports_are_read_only_and_fail_closed() -> None:
    import asyncio

    from account_intelligence.platform.ports import NotConfigured
    from connectors_client import notes

    for proto in (ports.ConnectorClient, ports.OAuthClient):
        names = {n for n, _ in inspect.getmembers(proto, inspect.isfunction)}
        assert not {
            n
            for n in names
            if any(
                w in n
                for w in (
                    "write",
                    "create",
                    "update",
                    "delete",
                    "send",
                    "upload",
                    "post",
                    "put",
                    "patch",
                    "move",
                    "share",
                )
            )
        }, names

    async def calls() -> None:
        for call in (
            ports.UnconfiguredOAuthClient().refresh("salesforce", "t"),
            ports.UnconfiguredConnectorClient().list_records("t", "Account", ("Id",), None),
            ports.UnconfiguredVault().ingest("t", "k"),
        ):
            with pytest.raises(NotConfigured):
                await call

    asyncio.run(calls())
    with pytest.raises(NotConfigured):
        notes.require_enabled()


# ---------------------------------------------------------------- end to end on PostgreSQL


async def _setup(
    plane: Plane,
    browser: Browser,
    kind: str = "salesforce",
    source: Any = None,
    oauth_scopes: tuple[str, ...] | None = None,
) -> tuple[UUID, FakeOAuth, Any]:
    t = new_tenant("ws6")
    enable_flags(ADMIN_DSN, [t])  # the connector routes are gated (api/flags.py)
    slug = f"acme-{uid()}"
    add_oidc_connection(t, plane, slug)
    add_member(t, f"{slug}:root", "admin")
    assert (
        await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    ).status_code == 303
    oauth = FakeOAuth(scopes=oauth_scopes or tuple(sorted(scopes.spec(kind).scopes)))
    tp = plane.app.state.container.tenant_plane
    tp.oauth, tp.connectors = oauth, source or FakeCrm()
    tp.connector_client_ids = {"salesforce": "sf-client", "hubspot": "hs-client"}
    return t, oauth, tp.connectors


async def _connect(browser: Browser, oauth: FakeOAuth, kind: str = "salesforce") -> str:
    started = await browser.call("POST", "/v1/connectors", json={"kind": kind})
    assert started.status_code == 201, started.text
    url = started.json()["authorize_url"]
    assert "code_challenge_method=S256" in url and "redirect_uri=https%3A%2F%2Fapp.test" in url
    state, code = oauth.consent(url)
    done = await browser.client.get(
        "/v1/connectors/oauth/callback", params={"state": state, "code": code}
    )
    assert done.status_code == 303, done.text
    return str(started.json()["connection_id"])


def _db(sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(ADMIN_DSN) as c:
        return c.execute(sql, params).fetchall()


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_connector_oauth_is_bound_minimal_and_fails_closed(
    plane: Plane, browser: Browser
) -> None:
    t, oauth, _ = await _setup(plane, browser)
    started = await browser.call("POST", "/v1/connectors", json={"kind": "salesforce"})
    state, code = oauth.consent(started.json()["authorize_url"])
    bad = await browser.client.get(
        "/v1/connectors/oauth/callback", params={"state": state + "x", "code": code}
    )
    assert bad.status_code == 409 and "unknown_or_used_state" in bad.text
    # another admin of the same tenant cannot finish this admin's consent
    other = Browser(plane)
    try:
        slug2 = f"acme-{uid()}"
        add_oidc_connection(t, plane, slug2)
        add_member(t, f"{slug2}:root2", "admin")
        await other.oidc_sign_in(slug2, "root2", extra={"groups": ["admins"]})
        stolen = await other.client.get(
            "/v1/connectors/oauth/callback", params={"state": state, "code": code}
        )
        # another user cannot even see the state (private workspaces, migration 0063)
        assert stolen.status_code == 409 and (
            "state_bound_to_another_user" in stolen.text or "unknown_or_used_state" in stolen.text
        )
    finally:
        await other.close()
    # the refused attempt rolled back: the owner finishes once, and a replay is refused
    owner = await browser.client.get(
        "/v1/connectors/oauth/callback", params={"state": state, "code": code}
    )
    assert owner.status_code == 303
    replay = await browser.client.get(
        "/v1/connectors/oauth/callback", params={"state": state, "code": code}
    )
    assert replay.status_code == 409 and "unknown_or_used_state" in replay.text
    # a source that grants more than the table asks for is refused
    oauth.scopes = (*oauth.scopes, "full")
    started = await browser.call("POST", "/v1/connectors", json={"kind": "salesforce"})
    state, code = oauth.consent(started.json()["authorize_url"])
    wide = await browser.client.get(
        "/v1/connectors/oauth/callback", params={"state": state, "code": code}
    )
    assert wide.status_code == 409 and "scope_mismatch" in wide.text
    # a source that is not in the table is refused before anything is stored
    unknown = await browser.call("POST", "/v1/connectors", json={"kind": "dropbox"})
    assert unknown.status_code == 422
    # the token is a reference in the database and never written anywhere else
    oauth.scopes = oauth.scopes[:-1]
    connection = await _connect(browser, oauth)
    refs = _db("SELECT token_ref FROM connector_connections WHERE id = %s", connection)
    assert refs[0][0] and TOKEN_CANARY not in refs[0][0]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_fetch_pass_is_bounded_logged_and_read_only(
    plane: Plane,
    browser: Browser,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    crm = FakeCrm(
        records={
            "Account": [{"Id": "001", "Name": "CONTENT-CANARY Co"}],
            "Opportunity": [{"Id": "006", "Name": "Renewal"}],
        }
    )
    t, oauth, _ = await _setup(plane, browser, source=crm)
    connection = await _connect(browser, oauth)
    for grant in (
        {"grant_kind": "crm_object", "resource_id": "Account"},
        {"grant_kind": "crm_field", "resource_id": "Account.Name"},
        {"grant_kind": "crm_object", "resource_id": "Opportunity"},
        {"grant_kind": "crm_field", "resource_id": "Opportunity.Name"},
    ):
        granted = await browser.call("POST", f"/v1/connectors/{connection}/grants", json=grant)
        assert granted.status_code == 201, granted.text
    for refused in (
        {"grant_kind": "crm_field", "resource_id": "Account.Secret"},  # not in the scope table
        {"grant_kind": "crm_object", "resource_id": "Lead"},
        {"grant_kind": "folder", "resource_id": "A"},  # no such grant kind
    ):
        assert (
            await browser.call("POST", f"/v1/connectors/{connection}/grants", json=refused)
        ).status_code == 422
    caplog.set_level(logging.DEBUG)
    from connectors_client import framework as fw
    from connectors_client import scopes as sc

    real = sc.load()
    monkeypatch.setattr(
        fw,
        "load",
        lambda: (real[0], real[1].__class__(**{**real[1].__dict__, "max_pages_per_pass": 1})),
    )
    first = (await browser.call("POST", f"/v1/connectors/{connection}/fetch")).json()
    assert first["exit_reason"] == "page_cap" and first["fetched"] == 1 and first["pages"] == 1
    monkeypatch.setattr(fw, "load", lambda: real)
    second = (await browser.call("POST", f"/v1/connectors/{connection}/fetch")).json()
    assert second["exit_reason"] == "complete" and second["fetched"] == 2
    fetched = {
        r[0]
        for r in _db("SELECT resource_id FROM connector_items WHERE connection_id = %s", connection)
    }
    assert fetched == {"Account/001", "Opportunity/006"}
    keys = [
        r[0]
        for r in _db("SELECT object_key FROM connector_items WHERE connection_id = %s", connection)
    ]
    assert all(k.startswith(f"tenants/{t}/connector-quarantine/") for k in keys)
    store = plane.app.state.container.tenant_plane.objects
    assert any(b"CONTENT-CANARY" in store.objects[k] for k in keys)
    assert not any(b"CRM-CANARY" in store.objects[k] for k in keys)  # extra fields dropped
    # every source call is in the access log
    logged = _db(
        "SELECT purpose, resource_id FROM connector_access_log WHERE"
        " connection_id = %s AND purpose IN ('list', 'fetch')",
        connection,
    )
    for kind, rid in crm.calls:
        assert (kind, rid) in logged, (kind, rid)
    # tokens and content never leave: logs, audit, access log, responses
    text_everywhere = " ".join(caplog.messages)
    audit_csv = (await browser.client.get("/v1/audit/export")).text
    rows = " ".join(
        str(r) for r in _db("SELECT * FROM connector_access_log WHERE tenant_id = %s", t)
    )
    for blob in (text_everywhere, audit_csv, rows, str(first), str(second)):
        assert TOKEN_CANARY not in blob and "CONTENT-CANARY" not in blob
    assert "connector_access" in audit_csv and "connector.fetch" in audit_csv
    # nothing from a connector is in any client table
    assert not _db(
        "SELECT 1 FROM view_rows WHERE tenant_id = %s AND cells::text LIKE '%%CONTENT-CANARY%%'", t
    )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_held_item_is_cleared_or_refused_once(plane: Plane, browser: Browser) -> None:
    from account_intelligence.db.session import tenant_txn
    from connectors_client import quarantine

    t, oauth, _ = await _setup(plane, browser)
    connection = await _connect(browser, oauth)
    async with tenant_txn(t) as s:
        for rid in ("held-1", "held-2"):
            assert await quarantine.hold(
                s,
                connection_id=UUID(connection),
                resource_id=rid,
                resource_kind="file",
                reason="outside_allow_list",
                now=plane.clock.now(),
            )
    items = (await browser.client.get(f"/v1/connectors/{connection}/quarantine")).json()["items"]
    assert {i["resource_id"]: i["status"] for i in items} == {"held-1": "held", "held-2": "held"}
    one, two = sorted(items, key=lambda i: i["resource_id"])
    path = f"/v1/connectors/{connection}/quarantine"
    assert (await browser.call("POST", f"{path}/{one['id']}/clear")).status_code == 200
    assert (await browser.call("POST", f"{path}/{one['id']}/clear")).status_code == 404
    assert (await browser.call("POST", f"{path}/{two['id']}/refuse")).status_code == 200
    assert (await browser.call("POST", f"{path}/{two['id']}/clear")).status_code == 404
    listed = (await browser.client.get(path)).json()["items"]
    assert {i["resource_id"]: i["status"] for i in listed} == {
        "held-1": "cleared",
        "held-2": "refused",
    }


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_revoked_or_widened_grants_disable_the_connection(
    plane: Plane, browser: Browser
) -> None:
    _, oauth, _ = await _setup(plane, browser)
    for mode, reason in (
        ("revoked", "token_revoked"),
        ("widen", "scope_changed"),
        ("flaky", "token_refresh_failed"),
    ):
        oauth.refresh_mode = "ok"
        connection = await _connect(browser, oauth)
        oauth.refresh_mode = mode
        before = oauth.refreshes
        out = (await browser.call("POST", f"/v1/connectors/{connection}/fetch")).json()
        assert out["exit_reason"] == f"broken:{reason}"
        assert oauth.refreshes - before <= scopes.load()[1].refresh_attempts
        assert _db(
            "SELECT status, broken_reason FROM connector_connections WHERE id = %s", connection
        )[0] == ("broken", reason)
        again = await browser.call("POST", f"/v1/connectors/{connection}/fetch")
        assert again.status_code == 409 and "connection_broken" in again.text


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_crm_fetch_keeps_only_granted_fields_and_disconnect_purges(
    plane: Plane, browser: Browser
) -> None:
    crm = FakeCrm(records={"Account": [{"Id": "001", "Name": "Synthetic Co", "Industry": "X"}]})
    _, oauth, _ = await _setup(plane, browser, kind="salesforce", source=crm)
    connection = await _connect(browser, oauth, "salesforce")
    for grant in (
        {"grant_kind": "crm_object", "resource_id": "Account"},
        {"grant_kind": "crm_field", "resource_id": "Account.Name"},
    ):
        assert (
            await browser.call("POST", f"/v1/connectors/{connection}/grants", json=grant)
        ).status_code == 201
    out = (await browser.call("POST", f"/v1/connectors/{connection}/fetch")).json()
    assert out["fetched"] == 1
    store = plane.app.state.container.tenant_plane.objects
    stored = [
        store.objects[r[0]]
        for r in _db("SELECT object_key FROM connector_items WHERE connection_id = %s", connection)
    ]
    assert stored and b"Synthetic Co" in stored[0]
    assert b"CRM-CANARY" not in stored[0] and b"Industry" not in stored[0]
    secrets = plane.app.state.container.tenant_plane.secrets
    ref = _db("SELECT token_ref FROM connector_connections WHERE id = %s", connection)[0][0]
    oauth.revoke_fails = True
    gone = (await browser.call("POST", f"/v1/connectors/{connection}/disconnect")).json()
    assert gone["status"] == "disconnected" and gone["upstream_revocation"] == "error"
    assert ref in secrets.destroyed
    assert _db("SELECT status, token_ref FROM connector_connections WHERE id = %s", connection)[
        0
    ] == ("disconnected", None)
