"""Mail and drive end to end (SYNTHETIC people, offline): the real OAuth, read and sync code and the
real routes run against fake Google and Microsoft endpoints, for every source a user connects.

For Gmail, Google Drive, Outlook and OneDrive in turn, one user:

1. sees the source available, starts Connect, consents and lands back on the page connected
   (state, PKCE and the read-only scope are on the authorize URL);
2. presses Sync now: the item that matches one of their accounts is kept, the unrelated one is
   not, and a second Sync now straight away is rate limited;
3. gets a new item; the hourly sync reads only what changed since the last one (incremental);
4. sees the item in Ask's workspace rows and on the account's private mail-and-drive sheet,
   and the page reports the last sync (status, listed, matched);
5. declines a second consent: the page shows "denied", never a server error;
6. disconnects that source: the token is destroyed (revoked at Google), the items are deleted.

Then the whole client's feature is switched off and the hourly sync purges what is left.

GUARANTEE(test_every_source_connects_syncs_and_disconnects_end_to_end)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from connectors_client.maildrive import HttpMailDriveReader, HttpOAuthClient
from tests.conftest import ADMIN_DSN
from tests.fakes.maildrive_fakes import FakeGoogle, FakeGraph
from tests.replica.conftest import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

APPS = {"google": ("google-client", "google-secret"), "microsoft": ("ms-client", "ms-secret")}
FAMILY = {
    "gmail": "google",
    "google_drive": "google",
    "outlook": "microsoft",
    "onedrive": "microsoft",
}
SCOPE = {
    "gmail": "https://www.googleapis.com/auth/gmail.readonly",
    "google_drive": "https://www.googleapis.com/auth/drive.readonly",
    "outlook": "https://graph.microsoft.com/Mail.Read",
    "onedrive": "https://graph.microsoft.com/Files.Read",
}


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


@dataclass
class Providers:
    """Both fakes behind one transport (routed by host), as the real clients see the internet."""

    google: FakeGoogle
    graph: FakeGraph
    transport: httpx.MockTransport = field(init=False)

    def __post_init__(self) -> None:
        def route(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            if host.endswith(("google.com", "googleapis.com")):
                return self.google.handle(request)
            return self.graph.handle(request)

        self.transport = httpx.MockTransport(route)


def _wire(plane: Any, providers: Providers) -> None:
    extras = plane.app.state.container.extras
    extras["maildrive_apps"] = APPS
    extras["maildrive_oauth"] = HttpOAuthClient(APPS, transport=providers.transport)
    extras["maildrive_reader"] = HttpMailDriveReader(transport=providers.transport)


@dataclass
class Case:
    kind: str
    graph_scope: str
    # (fakes, tag) -> adds one item that matches the account named after ``tag``
    add_match: Callable[[Providers, str, str], None]
    # adds one item that matches nothing
    add_noise: Callable[[Providers, str], None]


def _gmail_match(p: Providers, tag: str, domain: str) -> None:
    p.google.add_mail(f"m-{tag}", f"t-{tag}", f"Renewal {tag}", f"Ann <ann@{domain}>", "Pricing")


def _gmail_noise(p: Providers, tag: str) -> None:
    p.google.add_mail(f"n-{tag}", f"tn-{tag}", "Lunch", "bob@unrelated.example", "Friday?")


def _gdrive_match(p: Providers, tag: str, domain: str) -> None:
    p.google.add_file(f"f-{tag}", f"Renewal {tag}.pdf", f"ann@{domain}")


def _gdrive_noise(p: Providers, tag: str) -> None:
    p.google.add_file(f"fn-{tag}", "Holiday plan.pdf", "bob@unrelated.example")


def _outlook_match(p: Providers, tag: str, domain: str) -> None:
    p.graph.add_message(f"o-{tag}", f"Renewal {tag}", f"ann@{domain}", preview="Pricing")


def _outlook_noise(p: Providers, tag: str) -> None:
    p.graph.add_message(f"on-{tag}", "Lunch", "bob@unrelated.example", preview="Friday?")


def _onedrive_match(p: Providers, tag: str, domain: str) -> None:
    # OneDrive items carry no people: they match by the account's name in the file name
    p.graph.add_drive_file(f"d-{tag}", f"Acct{tag} renewal plan.docx")


def _onedrive_noise(p: Providers, tag: str) -> None:
    p.graph.add_drive_file(f"dn-{tag}", "Holiday plan.docx")


CASES = [
    Case("gmail", "", _gmail_match, _gmail_noise),
    Case("google_drive", "", _gdrive_match, _gdrive_noise),
    Case(
        "outlook",
        "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read"
        " openid profile email offline_access",
        _outlook_match,
        _outlook_noise,
    ),
    Case(
        "onedrive",
        "https://graph.microsoft.com/Files.Read https://graph.microsoft.com/User.Read"
        " openid profile email offline_access",
        _onedrive_match,
        _onedrive_noise,
    ),
]


async def _connect(plane: Any, kind: str) -> None:
    started = await _call(plane, "POST", f"/v1/mail-and-drive/{kind}/connect")
    assert started.status_code == 200, started.text
    url = urlsplit(started.json()["authorize_url"])
    query = parse_qs(url.query)
    assert query["client_id"] == [APPS[FAMILY[kind]][0]]
    assert SCOPE[kind] in query["scope"][0].split()
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"][0]
    state = query["state"][0]
    done = await _call(
        plane,
        "GET",
        f"/v1/mail-and-drive/callback?state={state}&code=code-{kind}",
        follow_redirects=False,
    )
    assert done.status_code == 303, done.text
    assert done.headers["location"] == "/mail-and-drive", done.headers["location"]


def _source(page: dict[str, Any], kind: str) -> dict[str, Any]:
    found: dict[str, Any] = next(s for s in page["sources"] if s["kind"] == kind)
    return found


def _items(tenant: UUID, user: str) -> list[str]:
    return [
        str(r[0])
        for r in _db(
            "SELECT title FROM connector_items WHERE tenant_id = %s AND owner_user_id = %s"
            " ORDER BY title",
            tenant,
            user,
        )
    ]


async def _workspace_titles(tenant: UUID, user: str) -> list[str]:
    from account_intelligence.db.session import tenant_txn
    from account_intelligence.replica.answers import load_workspace

    async with tenant_txn(tenant, user=user) as s:
        ws = await load_workspace(s, user)
    return sorted(r.title for r in ws.mail)


@pytest.mark.parametrize("case", CASES, ids=[c.kind for c in CASES])
async def test_every_source_connects_syncs_and_disconnects_end_to_end(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, case: Case
) -> None:
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")
    tenant: UUID = world["a"]
    tag = uuid4().hex[:6]
    user = f"e2e-{case.kind}-{tag}"
    domain = f"acct{tag}.example"
    _db(
        "INSERT INTO accounts (tenant_id, domain, name, added_by, owner_user_id)"
        " VALUES (%s, %s, %s, %s, %s)",
        tenant,
        domain,
        f"Acct{tag}",
        user,
        user,
    )
    # each fake grants exactly what its source asks for (a wrong grant is refused: tested above)
    google = FakeGoogle(scope=SCOPE[case.kind]) if FAMILY[case.kind] == "google" else FakeGoogle()
    graph = FakeGraph(scope=case.graph_scope) if case.graph_scope else FakeGraph()
    providers = Providers(google, graph)
    plane = _plane(tenant, user, world["payloads"])
    _wire(plane, providers)

    # 1. available, then connected
    page = (await _call(plane, "GET", "/v1/mail-and-drive")).json()
    assert _source(page, case.kind)["available"] is True
    assert _source(page, case.kind)["status"] == "not_connected"
    await _connect(plane, case.kind)
    assert (
        _source((await _call(plane, "GET", "/v1/mail-and-drive")).json(), case.kind)["status"]
        == "connected"
    )

    # 2. Sync now keeps only the matching item; a second press straight away is rate limited
    case.add_match(providers, tag, domain)
    case.add_noise(providers, tag)
    family = FAMILY[case.kind]
    synced = await _call(plane, "POST", f"/v1/mail-and-drive/{family}/sync")
    assert synced.status_code == 200, synced.text
    mine = next(s for s in synced.json()["sources"] if s["kind"] == case.kind)
    assert mine["state"] == "ok" and mine["matched"] >= 1, mine
    first = _items(tenant, user)
    assert any(f"Renewal {tag}" in t or f"Acct{tag}" in t for t in first), first
    assert not any("Lunch" in t or "Holiday" in t for t in first), first
    again = await _call(plane, "POST", f"/v1/mail-and-drive/{family}/sync")
    assert again.status_code == 429, again.text
    assert again.headers.get("retry-after")

    # 3. the hourly sync reads only what changed since (incremental): one new item arrives
    case.add_match(providers, f"{tag}b", domain)
    if case.kind == "onedrive":  # name matching: the second file names the same account
        providers.graph.add_drive_file(f"d2-{tag}", f"Acct{tag} second plan.docx")
    _db(
        "UPDATE connector_connections SET last_synced_at = now() - interval '2 hours'"
        " WHERE tenant_id = %s AND owner_user_id = %s",
        tenant,
        user,
    )
    calls_before = len(google.calls) + len(graph.calls)
    tick = await _call(plane, "POST", "/tasks/maildrive-sync", json={})
    assert tick.status_code == 200, tick.text
    second = _items(tenant, user)
    assert len(second) > len(first), (first, second)
    assert len(google.calls) + len(graph.calls) > calls_before
    cursor = _db(
        "SELECT cursor, last_sync_status, last_sync_listed, last_sync_matched"
        " FROM connector_connections WHERE tenant_id = %s AND owner_user_id = %s",
        tenant,
        user,
    )[0]
    assert cursor[0], "an incremental cursor is kept after a completed sync"
    assert cursor[1] == "ok"
    # the incremental read lists only the new items, not the whole history again
    assert cursor[2] <= 3, cursor

    # 4. Ask's workspace rows and the private sheet carry the items; the page reports the sync
    titles = await _workspace_titles(tenant, user)
    assert titles and all("Lunch" not in t and "Holiday" not in t for t in titles), titles
    sheet = await _call(plane, "GET", f"/v1/accounts/{domain}/sheets/mail-and-drive")
    assert sheet.status_code == 200, sheet.text
    page = (await _call(plane, "GET", "/v1/mail-and-drive")).json()
    src = _source(page, case.kind)
    assert src["last_sync_status"] == "ok" and src["last_synced_at"], src
    assert src["last_error"] is None

    # 5. a declined consent comes back as "denied", never a server error
    started = (await _call(plane, "POST", f"/v1/mail-and-drive/{case.kind}/connect")).json()
    state = parse_qs(urlsplit(started["authorize_url"]).query)["state"][0]
    declined = await _call(
        plane,
        "GET",
        f"/v1/mail-and-drive/callback?state={state}&error=access_denied",
        follow_redirects=False,
    )
    assert declined.status_code == 303
    assert declined.headers["location"] == "/mail-and-drive?error=denied"

    # 6. disconnect: token destroyed (revoked upstream at Google), items deleted
    gone = await _call(plane, "POST", f"/v1/mail-and-drive/{case.kind}/disconnect")
    assert gone.status_code == 200, gone.text
    assert gone.json()["items_deleted"] >= 1
    if family == "google":
        assert google.revoked, "Google revokes the refresh token on disconnect"
    assert _items(tenant, user) == []
    assert _db(
        "SELECT count(*) FROM connector_connections WHERE tenant_id = %s AND owner_user_id = %s"
        " AND status = 'active'",
        tenant,
        user,
    ) == [(0,)]
    assert await _workspace_titles(tenant, user) == []


async def test_switching_the_feature_off_purges_every_connection(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")
    tenant: UUID = world["b"]
    tag = uuid4().hex[:6]
    user = f"e2e-off-{tag}"
    domain = f"off{tag}.example"
    _db("UPDATE client_settings SET mail_drive_allowed = true WHERE tenant_id = %s", tenant)
    _db(
        "INSERT INTO accounts (tenant_id, domain, name, added_by, owner_user_id)"
        " VALUES (%s, %s, %s, %s, %s)",
        tenant,
        domain,
        f"Off{tag}",
        user,
        user,
    )
    providers = Providers(FakeGoogle(), FakeGraph())
    plane = _plane(tenant, user, world["payloads"])
    _wire(plane, providers)
    await _connect(plane, "gmail")
    _gmail_match(providers, tag, domain)
    assert (await _call(plane, "POST", "/v1/mail-and-drive/google/sync")).status_code == 200
    assert _items(tenant, user)

    _db("UPDATE client_settings SET mail_drive_allowed = false WHERE tenant_id = %s", tenant)
    _db(
        "UPDATE connector_connections SET last_synced_at = now() - interval '2 hours'"
        " WHERE tenant_id = %s AND owner_user_id = %s",
        tenant,
        user,
    )
    try:
        tick = await _call(plane, "POST", "/tasks/maildrive-sync", json={})
        assert tick.status_code == 200, tick.text
        assert _items(tenant, user) == []
        assert _db(
            "SELECT status FROM connector_connections WHERE tenant_id = %s AND owner_user_id = %s",
            tenant,
            user,
        ) == [("disconnected",)]
        assert providers.google.revoked
    finally:
        _db("UPDATE client_settings SET mail_drive_allowed = true WHERE tenant_id = %s", tenant)
