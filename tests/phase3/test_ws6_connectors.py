"""WS6 client history connectors (CN1 to CN4, D-65). SYNTHETIC sources only.

Lying scenarios: a tampered or replayed consent state, a consent finished by another admin, a
source that grants wider scopes (at consent or on refresh), a revoked grant, a shortcut in an
allowed folder pointing outside, a file with a second parent the caller cannot see, a shared drive
nobody named, a cycle, a CRM that returns fields nobody asked for, a fetch that would write, a
token that would end up in a log, an audit row or a response, connector content that would reach a
client table.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any
from uuid import UUID

import psycopg
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from connectors_client import drive, ports, scopes
from connectors_client.ports import DriveMeta
from tests.conftest import ADMIN_DSN
from tests.fakes.connectors import FOLDER, TOKEN_CANARY, FakeCrm, FakeDrive, FakeOAuth
from tests.phase3.conftest import (
    Browser,
    Plane,
    add_member,
    add_oidc_connection,
    new_tenant,
    uid,
)

DEPTH = 20


def _f(
    fid: str,
    *parents: str,
    drive_id: str | None = None,
    shortcut: str | None = None,
    folder: bool = False,
) -> DriveMeta:
    return DriveMeta(fid, fid, FOLDER if folder else "text/plain", parents, drive_id, shortcut)


def _metas(*nodes: DriveMeta) -> dict[str, DriveMeta | None]:
    return {n.id: n for n in nodes}


ROOT = _f("root", folder=True)
ALLOWED = _f("A", "root", folder=True)
OUTSIDE = _f("O", "root", folder=True)


@pytest.mark.parametrize(
    ("node", "extra", "allowed", "reason"),
    [
        (_f("a", "A"), (), True, None),
        (_f("b", "S"), (_f("S", "A", folder=True),), True, None),
        (_f("o", "O"), (), False, "outside_allow_list"),
        (_f("m", "A", "O"), (), True, None),  # several parents, one named
        (_f("u", "A", "hidden"), (), False, "unknown_ancestry"),  # one parent invisible
        (_f("s", "A", shortcut="o2"), (_f("o2", "O"),), False, "shortcut_outside"),
        (_f("s2", "O", shortcut="a2"), (_f("a2", "A"),), True, None),
        (
            _f("d", "SD", drive_id="SD"),
            (_f("SD", drive_id="SD", folder=True),),
            False,
            "outside_allow_list",
        ),  # a shared drive nobody named
        (_f("c1", "c2"), (_f("c2", "c1", folder=True),), False, "unknown_ancestry"),  # a cycle
        (
            _f("deep", "L19"),
            tuple(_f(f"L{i}", f"L{i - 1}" if i else "O", folder=True) for i in range(20)),
            False,
            "unknown_ancestry",
        ),
    ],
)
def test_drive_ancestry_denies_everything_outside_the_named_folders(
    node: DriveMeta, extra: tuple[DriveMeta, ...], allowed: bool, reason: str | None
) -> None:
    metas = _metas(ROOT, ALLOWED, OUTSIDE, node, *extra)
    verdict = drive.decide(node.id, metas, frozenset({"A"}), DEPTH)
    assert (verdict.allowed, verdict.reason) == (allowed, reason)
    assert not drive.decide("nobody", metas, frozenset({"A"}), DEPTH).allowed
    shared = _metas(_f("SD", drive_id="SD", folder=True), _f("x", "SD", drive_id="SD"))
    assert drive.decide("x", shared, frozenset({"SD"}), DEPTH).allowed  # the named shared drive


@settings(max_examples=150, deadline=None)
@given(
    st.dictionaries(
        st.sampled_from([f"n{i}" for i in range(8)]),
        st.lists(st.sampled_from(["root", "A", "O"] + [f"n{i}" for i in range(8)]), max_size=2),
        min_size=1,
    )
)
def test_ancestry_matches_a_brute_force_oracle(tree: dict[str, list[str]]) -> None:
    metas: dict[str, DriveMeta | None] = {"root": ROOT, "A": ALLOWED, "O": OUTSIDE}
    for fid, parents in tree.items():
        metas[fid] = _f(fid, *parents)

    def oracle(node: str, path: frozenset[str]) -> str:
        if node == "A":
            return "allowed"
        if node in path or node not in metas or metas[node] is None:
            return "unknown"
        meta = metas[node]
        assert meta is not None
        if not meta.parents:
            return "outside"
        got = {oracle(p, path | {node}) for p in meta.parents}
        return "unknown" if "unknown" in got else ("allowed" if "allowed" in got else "outside")

    for fid in tree:
        expected = oracle(fid, frozenset())
        verdict = drive.decide(fid, metas, frozenset({"A"}), DEPTH)
        assert verdict.allowed == (expected == "allowed"), (fid, tree)
        if expected == "unknown":
            assert verdict.reason == "unknown_ancestry"


def test_scopes_are_exactly_the_table_and_crm_fields_are_allow_listed() -> None:
    drive_scopes = scopes.spec("google_drive").scopes
    assert scopes.scopes_ok("google_drive", tuple(drive_scopes))
    assert not scopes.scopes_ok("google_drive", (*drive_scopes, "https://mail.google.com/"))
    assert not scopes.scopes_ok("google_drive", ())
    assert scopes.allowed_fields(
        "salesforce", "Account", {"Account"}, {"Account.Name", "Account.Secret"}
    ) == ("Name",)
    assert scopes.allowed_fields("salesforce", "Account", set(), {"Account.Name"}) == ()
    assert scopes.allowed_fields("salesforce", "Lead", {"Lead"}, {"Lead.Email"}) == ()
    # Gmail is a personal Mail and drive source (REPLICA_PROMPT); call notes stay off (D-65)
    assert scopes.spec("gmail").family == "mail" and not scopes.spec("call_notes").enabled


def test_connector_ports_are_read_only_and_fail_closed() -> None:
    import asyncio

    from account_intelligence.platform.ports import NotConfigured
    from connectors_client import gmail, notes

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
            ports.UnconfiguredConnectorClient().list_folder("t", "f", None),
            ports.UnconfiguredConnectorClient().fetch("t", "f", 1),
            ports.UnconfiguredVault().ingest("t", "k"),
        ):
            with pytest.raises(NotConfigured):
                await call

    asyncio.run(calls())
    for module in (gmail, notes):
        with pytest.raises(NotConfigured):
            module.require_enabled()


# ---------------------------------------------------------------- end to end on PostgreSQL


async def _setup(
    plane: Plane,
    browser: Browser,
    kind: str = "google_drive",
    source: Any = None,
    oauth_scopes: tuple[str, ...] | None = None,
) -> tuple[UUID, FakeOAuth, Any]:
    t = new_tenant("ws6")
    slug = f"acme-{uid()}"
    add_oidc_connection(t, plane, slug)
    add_member(t, f"{slug}:root", "admin")
    assert (
        await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    ).status_code == 303
    oauth = FakeOAuth(scopes=oauth_scopes or tuple(sorted(scopes.spec(kind).scopes)))
    tp = plane.app.state.container.tenant_plane
    tp.oauth, tp.connectors = oauth, source or FakeDrive()
    tp.connector_client_ids = {
        "google_drive": "drive-client",
        "salesforce": "sf-client",
        "hubspot": "hs-client",
    }
    return t, oauth, tp.connectors


async def _connect(browser: Browser, oauth: FakeOAuth, kind: str = "google_drive") -> str:
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
    started = await browser.call("POST", "/v1/connectors", json={"kind": "google_drive"})
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
        assert stolen.status_code == 409 and "state_bound_to_another_user" in stolen.text
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
    oauth.scopes = (*oauth.scopes, "https://www.googleapis.com/auth/drive")
    started = await browser.call("POST", "/v1/connectors", json={"kind": "google_drive"})
    state, code = oauth.consent(started.json()["authorize_url"])
    wide = await browser.client.get(
        "/v1/connectors/oauth/callback", params={"state": state, "code": code}
    )
    assert wide.status_code == 409 and "scope_mismatch" in wide.text
    assert (await browser.call("POST", "/v1/connectors", json={"kind": "gmail"})).status_code == 409
    # the token is a reference in the database and never written anywhere else
    oauth.scopes = oauth.scopes[:1]
    connection = await _connect(browser, oauth)
    refs = _db("SELECT token_ref FROM connector_connections WHERE id = %s", connection)
    assert refs[0][0] and TOKEN_CANARY not in refs[0][0]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_fetch_pass_is_bounded_resumable_logged_and_read_only(
    plane: Plane,
    browser: Browser,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    src = FakeDrive()
    src.folder("root")
    src.folder("A", "root")
    src.folder("O", "root")
    src.folder("S", "A")
    src.file("a1", "A", body=b"CONTENT-CANARY a1")
    src.file("a2", "A")
    src.file("s1", "S")
    src.file("multi", "A", "O")
    src.file("half", "A", "ghost")
    src.hidden.add("ghost")
    src.file("out", "O", body=b"never read")
    src.file("short", "A", shortcut="out")
    t, oauth, _ = await _setup(plane, browser, source=src)
    connection = await _connect(browser, oauth)
    granted = await browser.call(
        "POST",
        f"/v1/connectors/{connection}/grants",
        json={"grant_kind": "drive_folder", "resource_id": "A"},
    )
    assert granted.status_code == 201
    assert (
        await browser.call(
            "POST",
            f"/v1/connectors/{connection}/grants",
            json={"grant_kind": "crm_field", "resource_id": "A.B"},
        )
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
    assert first["exit_reason"] == "page_cap"
    assert _db("SELECT cursor FROM connector_connections WHERE id = %s", connection)[0][0]
    monkeypatch.setattr(fw, "load", lambda: real)
    second = (await browser.call("POST", f"/v1/connectors/{connection}/fetch")).json()
    assert second["exit_reason"] == "complete"
    fetched = {
        r[0]
        for r in _db("SELECT resource_id FROM connector_items WHERE connection_id = %s", connection)
    }
    assert fetched == {"a1", "a2", "s1", "multi"}
    held = dict(
        _db(
            "SELECT resource_id, reason FROM connector_quarantine WHERE connection_id = %s",
            connection,
        )
    )
    assert held == {"half": "unknown_ancestry", "short": "shortcut_outside"}
    assert ("fetch", "out") not in src.calls and ("fetch", "half") not in src.calls
    keys = [
        r[0]
        for r in _db("SELECT object_key FROM connector_items WHERE connection_id = %s", connection)
    ]
    assert all(k.startswith(f"tenants/{t}/connector-quarantine/") for k in keys)
    store = plane.app.state.container.tenant_plane.objects
    assert any(b"CONTENT-CANARY" in store.objects[k] for k in keys)
    # every source call is in the access log
    logged = _db(
        "SELECT purpose, resource_id FROM connector_access_log WHERE"
        " connection_id = %s AND purpose IN ('list', 'metadata', 'fetch')",
        connection,
    )
    for kind, rid in src.calls:
        purpose = {"list": "list", "meta": "metadata", "fetch": "fetch"}[kind]
        assert (purpose, rid) in logged, (kind, rid)
    # an admin clears the shortcut: the next pass reads its target
    items = (await browser.client.get(f"/v1/connectors/{connection}/quarantine")).json()["items"]
    short = next(i for i in items if i["resource_id"] == "short")
    assert (
        await browser.call("POST", f"/v1/connectors/{connection}/quarantine/{short['id']}/clear")
    ).status_code == 200
    assert (
        await browser.call("POST", f"/v1/connectors/{connection}/quarantine/{short['id']}/clear")
    ).status_code == 404
    await browser.call("POST", f"/v1/connectors/{connection}/fetch")
    assert ("fetch", "out") in src.calls
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
