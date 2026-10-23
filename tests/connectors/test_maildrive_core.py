"""Mail and drive connector core (SYNTHETIC, offline): consent scopes as the identity platforms
really report them, refresh token rotation, the bounded and resumable read pass (paging, item
limit, incremental cursors, stale cursors, throttling, an expired access token) and the sync's
recorded state. Every provider is a fake (``tests/fakes/maildrive_fakes.py``); nothing reaches a
network.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.api.tenant_plane import DevelopmentSecretStore
from connectors_client import maildrive
from connectors_client import oauth as consent
from connectors_client.maildrive import (
    Account,
    HttpMailDriveReader,
    HttpOAuthClient,
    Item,
    ReadResult,
    matches,
)
from connectors_client.ports import SourceError
from connectors_client.scopes import scopes_ok
from tenancy.ports import UnconfiguredSecretStore
from tests.conftest import ADMIN_DSN
from tests.fakes.maildrive_fakes import GMAIL_SCOPE, FakeGoogle, FakeGraph, RecordingSleep

pytestmark = pytest.mark.asyncio
MS_APPS = {"microsoft": ("ms-client", "ms-secret")}
GOOGLE_APPS = {"google": ("g-client", "g-secret")}
MAIL_READ = "https://graph.microsoft.com/Mail.Read"


def _since(days: int = 30) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _ids(result: ReadResult) -> list[str]:
    return [i.resource_id for i in result.items]


# --- scopes -------------------------------------------------------------------------------


async def test_scope_check_ignores_sign_in_scopes_and_normalises_graph_urls() -> None:
    granted = (MAIL_READ, "https://graph.microsoft.com/User.Read", "openid", "profile", "email")
    assert scopes_ok("outlook", granted)
    assert scopes_ok("outlook", ("Mail.Read", "offline_access"))  # short form, any case
    assert scopes_ok("outlook", ("mail.read",))
    assert not scopes_ok("outlook", ("User.Read", "openid"))  # the data scope is missing
    assert not scopes_ok("outlook", (MAIL_READ, "https://graph.microsoft.com/Mail.ReadWrite"))
    assert scopes_ok(
        "gmail", (GMAIL_SCOPE, "openid", "https://www.googleapis.com/auth/userinfo.email")
    )
    assert not scopes_ok("gmail", (GMAIL_SCOPE, "https://mail.google.com/"))  # wider: refused


# --- matching -----------------------------------------------------------------------------


async def test_owner_domain_and_free_mail_never_match_by_domain() -> None:
    mine, webmail, acme = uuid4(), uuid4(), uuid4()
    accounts = [
        Account(mine, "mycorp.example", "Mycorp"),
        Account(webmail, "gmail.com", "Webmail"),
        Account(acme, "acme.example", "Acme Robotics"),
    ]
    colleague = Item("r1", "thread", "Hi", "", None, ("pat@eu.mycorp.example",))
    personal = Item("r2", "thread", "Hi", "", None, ("someone@gmail.com",))
    client = Item("r3", "thread", "Hi", "", None, ("pat@mycorp.example", "jo@acme.example"))
    named = Item("r4", "thread", "Mycorp offsite", "", None, ("pat@mycorp.example",))
    assert matches(colleague, accounts, exclude_domains=("mycorp.example",)) == []
    assert matches(personal, accounts) == []
    assert matches(client, accounts, exclude_domains=("mycorp.example",)) == [acme]
    # name matching is unchanged
    assert matches(named, accounts, exclude_domains=("mycorp.example",)) == [mine]


# --- reader: paging, limit, cursors -------------------------------------------------------


def _google(page_size: int = 2, extra: int = 5) -> FakeGoogle:
    google = FakeGoogle(page_size=page_size)
    for n in range(extra):
        google.add_mail(f"a{n}", f"ta{n}", f"Renewal {n}", f"jo{n}@acme.example")
    return google


async def test_gmail_pages_to_completion_then_reads_only_new_mail() -> None:
    google = _google()
    reader = HttpMailDriveReader(transport=google.transport)
    first = await reader.read("gmail", "access-1", since=_since(), cursor=None, limit=50)
    assert first.complete and len(first.items) == 7 and first.pages == 4
    assert json.loads(str(first.cursor))["mode"] == "history"
    google.add_mail("n1", "tn1", "New thread", "kim@acme.example")
    google.add_mail("n2", "tn2", "Spam", "x@acme.example", labels=("SPAM",))
    second = await reader.read("gmail", "access-1", since=_since(), cursor=first.cursor)
    assert second.complete and _ids(second) == ["tn1"]
    assert not any(p.endswith("/messages") for p in google.calls[-3:])  # history, not a list


async def test_partial_at_the_limit_resumes_from_the_cursor() -> None:
    google = _google()
    reader = HttpMailDriveReader(transport=google.transport)
    first = await reader.read("gmail", "access-1", since=_since(), cursor=None, limit=3)
    assert not first.complete and len(first.items) == 3 and first.cursor
    second = await reader.read("gmail", "access-1", since=_since(), cursor=first.cursor, limit=3)
    assert not second.complete and len(second.items) == 3
    third = await reader.read("gmail", "access-1", since=_since(), cursor=second.cursor, limit=3)
    assert third.complete and len(third.items) == 1
    seen = _ids(first) + _ids(second) + _ids(third)
    assert len(seen) == len(set(seen)) == 7  # every thread once, none skipped


async def test_page_cap_stops_a_pass_and_the_cursor_resumes_it() -> None:
    google = _google()
    reader = HttpMailDriveReader(transport=google.transport, max_pages=2)
    first = await reader.read("gmail", "access-1", since=_since(), cursor=None, limit=50)
    assert not first.complete and first.pages == 2 and len(first.items) == 4
    rest = await reader.read("gmail", "access-1", since=_since(), cursor=first.cursor, limit=50)
    assert rest.complete and len(set(_ids(first) + _ids(rest))) == 7


async def test_stale_gmail_history_resyncs_from_the_window() -> None:
    google = _google(page_size=50)
    reader = HttpMailDriveReader(transport=google.transport)
    first = await reader.read("gmail", "access-1", since=_since(), cursor=None)
    google.oldest_history = 10_000  # the stored historyId is now too old (404)
    again = await reader.read("gmail", "access-1", since=_since(), cursor=first.cursor)
    assert again.complete and len(again.items) == 7
    assert json.loads(str(again.cursor))["mode"] == "history"


async def test_outlook_delta_pages_folders_and_is_incremental() -> None:
    graph = FakeGraph(page_size=2)
    for n in range(3):
        graph.add_message(f"i{n}", f"In {n}", f"jo{n}@acme.example")
    graph.add_message("s0", "Sent", "me@mycorp.example", folder="sentitems", to=("a@acme.example",))
    graph.add_message("old", "Old", "x@acme.example", when=_since(60))  # outside the window
    graph.issued = 1
    reader = HttpMailDriveReader(transport=graph.transport)
    first = await reader.read("outlook", graph.access, since=_since(), cursor=None, limit=50)
    assert first.complete
    assert sorted(_ids(first)) == ["conv-i0", "conv-i1", "conv-i2", "conv-s0"]
    assert set(json.loads(str(first.cursor))["links"]) == {"inbox", "sentitems"}
    graph.add_message("i9", "Fresh", "kim@acme.example")
    second = await reader.read("outlook", graph.access, since=_since(), cursor=first.cursor)
    assert second.complete and _ids(second) == ["conv-i9"]


async def test_outlook_partial_resumes_and_an_expired_delta_resyncs() -> None:
    graph = FakeGraph(page_size=2)
    for n in range(3):
        graph.add_message(f"i{n}", f"In {n}", f"jo{n}@acme.example")
    graph.add_message("s0", "Sent", "me@mycorp.example", folder="sentitems")
    graph.issued = 1
    reader = HttpMailDriveReader(transport=graph.transport)
    part = await reader.read("outlook", graph.access, since=_since(), cursor=None, limit=3)
    assert not part.complete and len(part.items) == 3
    rest = await reader.read("outlook", graph.access, since=_since(), cursor=part.cursor)
    assert rest.complete and _ids(rest) == ["conv-s0"]
    graph.expired.update({str(graph.folders["inbox"].seq)})  # the inbox delta token expired
    again = await reader.read("outlook", graph.access, since=_since(), cursor=rest.cursor)
    assert again.complete and len(again.items) == 4  # resynced from the window


async def test_google_drive_and_onedrive_are_incremental_and_skip_folders() -> None:
    google = FakeGoogle(page_size=2)
    for n in range(3):
        google.add_file(f"f{n}", f"Acme plan {n}.pdf", "jo@acme.example")
    google.access = "access-1"
    reader = HttpMailDriveReader(transport=google.transport)
    first = await reader.read("google_drive", "access-1", since=_since(), cursor=None)
    assert first.complete and _ids(first) == ["f0", "f1", "f2"]
    google.add_file("f9", "New.pdf", "jo@acme.example")
    second = await reader.read("google_drive", "access-1", since=_since(), cursor=first.cursor)
    assert second.complete and _ids(second) == ["f9"]

    graph = FakeGraph(page_size=2)
    graph.add_drive_file("d0", "Acme deck.pptx")
    graph.add_drive_file("dir", "Acme", folder=True)
    graph.add_drive_file("old", "Old.docx", when=_since(90))
    graph.issued = 1
    graph_reader = HttpMailDriveReader(transport=graph.transport)
    first = await graph_reader.read("onedrive", graph.access, since=_since(), cursor=None)
    assert first.complete and _ids(first) == ["d0"]
    graph.add_drive_file("d1", "Acme notes.docx")
    second = await graph_reader.read("onedrive", graph.access, since=_since(), cursor=first.cursor)
    assert second.complete and _ids(second) == ["d1"]


async def test_sharepoint_keeps_files_used_since_the_last_sync() -> None:
    graph = FakeGraph(page_size=1)
    graph.add_used("u1", "Acme QBR.pptx")
    graph.add_used("u2", "Old.docx", when=_since(10))
    graph.issued = 1
    reader = HttpMailDriveReader(transport=graph.transport)
    page = await reader.read("sharepoint", graph.access, since=_since(2), cursor=None)
    assert page.complete and _ids(page) == ["u1"] and page.cursor is None


# --- reader: throttling, 401, hosts -------------------------------------------------------


async def test_throttling_waits_retry_after_and_gives_up_bounded() -> None:
    google = _google(page_size=50)
    google.fail = deque([429])
    sleep = RecordingSleep()
    reader = HttpMailDriveReader(transport=google.transport, sleep=sleep)
    page = await reader.read("gmail", "access-1", since=_since(), cursor=None)
    assert page.complete and len(page.items) == 7
    assert sleep.waits == [7.0] and page.waited_s == 7.0

    google.fail = deque([503] * maildrive.MAX_ATTEMPTS)
    sleep.waits.clear()
    with pytest.raises(SourceError) as down:
        await reader.read("gmail", "access-1", since=_since(), cursor=None)
    assert down.value.code == "provider_unavailable"
    assert sleep.waits == [1.0, 2.0, 4.0]  # MAX_ATTEMPTS - 1 waits, never unbounded


async def test_retry_after_is_capped_and_an_expired_token_is_raised() -> None:
    import httpx

    long = httpx.Response(429, headers={"Retry-After": "3600"})
    assert maildrive._retry_after(long, 0) == maildrive.MAX_WAIT_S
    google = _google()
    google.fail = deque([401])
    with pytest.raises(maildrive.TokenExpired):
        await HttpMailDriveReader(transport=google.transport).read(
            "gmail", "access-1", since=_since(), cursor=None
        )
    with pytest.raises(SourceError):
        maildrive._graph_link("https://graph.microsoft.com/v1.0", "https://evil.example/next")


# --- consent and sync against the database ------------------------------------------------


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


@pytest.fixture(scope="module")
def tenant() -> UUID:
    tid = uuid4()
    _db("INSERT INTO tenants (id, name) VALUES (%s, %s)", tid, f"Maildrive {tid.hex[:6]}")
    _db("INSERT INTO wallets (tenant_id, balance) VALUES (%s, 0)", tid)
    return tid


def _owner(tenant: UUID, email: str | None = None) -> str:
    user = f"md-{uuid4().hex[:8]}"
    _db(
        "INSERT INTO users (tenant_id, subject, email) VALUES (%s, %s, %s)",
        tenant,
        user,
        email or f"{user}@mycorp.example",
    )
    for domain in ("acme.example", "mycorp.example"):
        _db(
            "INSERT INTO accounts (tenant_id, domain, added_by, owner_user_id)"
            " VALUES (%s, %s, %s, %s)",
            tenant,
            domain,
            user,
            user,
        )
    return user


async def _connection(
    tenant: UUID,
    user: str,
    kind: str,
    store: Any,
    refresh: str = "ms-refresh-0",
    scopes: tuple[str, ...] = (MAIL_READ,),
) -> UUID:
    conn = uuid4()
    ref = await store.put(
        f"tenants/{tenant}/connectors/{conn}", json.dumps({"refresh_token": refresh}).encode()
    )
    provider = "google" if kind in ("gmail", "google_drive") else "microsoft"
    _db(
        "INSERT INTO connector_connections (id, tenant_id, kind, provider, status, owner_user_id,"
        " scopes, token_ref, created_by, created_at) VALUES (%s, %s, %s, %s, 'active', %s, %s,"
        " %s, %s, now())",
        conn, tenant, kind, provider, user, list(scopes), ref, user,
    )  # fmt: skip
    return conn


def _state(conn: UUID) -> tuple[Any, ...]:
    return tuple(
        _db(
            "SELECT status, broken_reason, last_sync_status, last_error, last_error_at IS NOT NULL,"
            " last_sync_listed, last_sync_matched, last_synced_at IS NOT NULL, cursor IS NOT NULL"
            " FROM connector_connections WHERE id = %s",
            conn,
        )[0]
    )


async def _sync(tenant: UUID, conn: UUID, oauth: Any, reader: Any, store: Any, **kw: Any) -> Any:
    from account_intelligence.db.session import tenant_txn

    return await maildrive.sync_connection(
        tenant_txn, tenant, conn, oauth=oauth, reader=reader, store=store,
        now=datetime.now(UTC), apps=kw.pop("apps", MS_APPS), **kw,
    )  # fmt: skip


@pytest.mark.requires_postgres
async def test_microsoft_consent_accepts_sign_in_scopes_and_refuses_a_missing_one(
    tenant: UUID,
) -> None:
    from account_intelligence.db.session import tenant_txn

    store = DevelopmentSecretStore()

    async def consent_with(graph: FakeGraph) -> UUID:
        user = f"md-{uuid4().hex[:8]}"
        async with tenant_txn(tenant) as s:
            started = await consent.start(
                s, kind="outlook", user=user, client_id="ms-client",
                redirect_uri="https://app.test/cb", now=datetime.now(UTC), personal=True,
            )  # fmt: skip
        state = parse_qs(urlsplit(started.authorize_url).query)["state"][0]
        async with tenant_txn(tenant) as s:
            return await consent.complete(
                s, state=state, code="c1", user=user, redirect_uri="https://app.test/cb",
                oauth=HttpOAuthClient(MS_APPS, transport=graph.transport), store=store,
                tenant_id=tenant, now=datetime.now(UTC),
            )  # fmt: skip

    connected = await consent_with(FakeGraph())  # full URLs, User.Read, openid, offline_access
    assert _db("SELECT status FROM connector_connections WHERE id = %s", connected) == [("active",)]
    for graph, reason in (
        (FakeGraph(scope="https://graph.microsoft.com/User.Read openid"), "scope_mismatch"),
        (FakeGraph(scope=f"{MAIL_READ} https://graph.microsoft.com/Mail.Send"), "scope_mismatch"),
    ):
        with pytest.raises(consent.ConnectorRefused) as refused:
            await consent_with(graph)
        assert refused.value.reason == reason
    down = FakeGraph()
    down.token_fail = deque([503])
    with pytest.raises(consent.ConnectorRefused) as refused:
        await consent_with(down)
    assert refused.value.reason == "provider_unavailable" and refused.value.transient
    bare = FakeGraph()
    bare.no_refresh = True
    with pytest.raises(consent.ConnectorRefused) as refused:
        await consent_with(bare)
    assert refused.value.reason == "no_refresh_token"


@pytest.mark.requires_postgres
async def test_sync_persists_a_rotated_refresh_token_and_refreshes_once_on_401(
    tenant: UUID,
) -> None:
    store = DevelopmentSecretStore()
    user = _owner(tenant)
    conn = await _connection(tenant, user, "outlook", store)
    graph = FakeGraph()
    graph.add_message("m1", "Acme pilot", "jo@acme.example")
    graph.add_message("m2", "Team lunch", "pat@mycorp.example")  # the owner's own domain
    graph.fail = deque([401])  # the first access token is refused once
    oauth = HttpOAuthClient(MS_APPS, transport=graph.transport)
    reader = HttpMailDriveReader(transport=graph.transport, sleep=RecordingSleep())
    result = await _sync(tenant, conn, oauth, reader, store)
    assert (result.state, result.listed, result.matched) == ("ok", 2, 1)
    # the second refresh used the token the first one rotated in: it was written back
    assert [f["refresh_token"] for f in graph.token_forms] == ["ms-refresh-0", "ms-refresh-1"]
    ref = _db("SELECT token_ref FROM connector_connections WHERE id = %s", conn)[0][0]
    assert json.loads(await store.read(ref)) == {"refresh_token": "ms-refresh-2"}
    kept = [k for k in store._values if str(k).startswith(f"tenants/{tenant}/connectors/{conn}")]
    assert kept == [ref]  # the superseded versions are destroyed
    assert _state(conn) == ("active", None, "ok", None, False, 2, 1, True, True)
    logged = _db("SELECT purpose, outcome FROM connector_access_log WHERE connection_id = %s", conn)
    assert ("list", "denied") in logged and ("list", "ok") in logged
    assert logged.count(("token_refresh", "ok")) == 2

    graph.fail = deque([401, 401])  # refused again after the refresh: the grant is gone
    result = await _sync(tenant, conn, oauth, reader, store)
    assert (result.state, result.broken) == ("broken", "token_revoked")
    assert _state(conn)[:4] == ("broken", "token_revoked", "failed", "token_revoked")


@pytest.mark.requires_postgres
async def test_sync_records_failures_without_breaking_the_grant(tenant: UUID) -> None:
    store = DevelopmentSecretStore()
    user = _owner(tenant)
    conn = await _connection(tenant, user, "outlook", store)
    graph = FakeGraph()
    graph.add_message("m1", "Acme pilot", "jo@acme.example")
    oauth = HttpOAuthClient(MS_APPS, transport=graph.transport)
    reader = HttpMailDriveReader(transport=graph.transport, sleep=RecordingSleep())

    graph.fail = deque([503] * maildrive.MAX_ATTEMPTS)  # the provider is down
    result = await _sync(tenant, conn, oauth, reader, store)
    assert (result.state, result.reason, result.broken) == ("failed", "provider_unavailable", None)
    failed = ("active", None, "failed", "provider_unavailable", True, 0, 0, False, False)
    assert _state(conn) == failed

    graph.token_fail = deque([503] * 3)  # the token endpoint is down: transient, still active
    result = await _sync(tenant, conn, oauth, reader, store)
    assert (result.state, result.reason) == ("failed", "token_refresh_failed")
    assert _state(conn)[:4] == ("active", None, "failed", "token_refresh_failed")

    result = await _sync(tenant, conn, oauth, reader, store)  # recovered
    assert (result.state, result.matched) == ("ok", 1)
    assert _state(conn) == ("active", None, "ok", None, False, 1, 1, True, True)

    skipped = await _sync(tenant, conn, oauth, reader, UnconfiguredSecretStore())
    assert (skipped.state, skipped.skipped, skipped.reason) == ("skipped", True, "not_configured")
    assert _state(conn)[2] == "ok"  # nothing recorded for a skipped sync


@pytest.mark.requires_postgres
async def test_a_partial_sync_saves_the_cursor_and_the_next_one_finishes(tenant: UUID) -> None:
    store = DevelopmentSecretStore()
    user = _owner(tenant)
    conn = await _connection(tenant, user, "gmail", store, "refresh-0", (GMAIL_SCOPE,))
    google = _google(page_size=2)
    oauth = HttpOAuthClient(GOOGLE_APPS, transport=google.transport)
    reader = HttpMailDriveReader(transport=google.transport)
    first = await _sync(tenant, conn, oauth, reader, store, apps=GOOGLE_APPS, limit=4)
    assert (first.state, first.listed) == ("partial", 4)
    assert _state(conn)[2:] == ("partial", None, False, 4, first.matched, False, True)
    second = await _sync(tenant, conn, oauth, reader, store, apps=GOOGLE_APPS, limit=4)
    assert (second.state, second.listed) == ("ok", 3)
    assert _state(conn)[7:] == (True, True)
    acme = _db(
        "SELECT count(DISTINCT i.resource_id) FROM connector_items i JOIN accounts a"
        " ON a.id = i.account_id WHERE i.connection_id = %s AND a.domain = 'acme.example'",
        conn,
    )
    assert acme == [(5,)]  # every acme thread once, across the two passes
    google.add_mail("n1", "tn1", "Acme follow-up", "kim@acme.example")
    third = await _sync(tenant, conn, oauth, reader, store, apps=GOOGLE_APPS, limit=4)
    assert (third.state, third.listed, third.matched) == ("ok", 1, 1)  # only the new thread
